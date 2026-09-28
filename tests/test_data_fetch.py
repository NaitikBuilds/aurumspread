"""Tests for aurumspread.data.fetch (T02: fetch, cache, date validation). No network.

Payloads here only exercise the fetch mechanism (Date column handling, caching,
retries); the numeric cells are placeholders, not market data. Real Bhavcopy
rows for parser/normalization tests live in tests/fixtures/.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pytest

from aurumspread.config import ConfigError, DataSourceConfig
from aurumspread.data.dq import DQLog
from aurumspread.data.fetch import (
    BhavcopyRequest,
    RateLimiter,
    TransportError,
    build_request,
    fetch_bhavcopy,
    fetch_range,
    raw_path,
    returned_trade_dates,
)
from aurumspread.data.parse import BhavcopyFormatError

HEADER = "Symbol,Date,ExpiryDate,Open,High,Low,Close,Volume,OpenInterest"
THU = date(2026, 9, 3)
FRI = date(2026, 9, 4)
MON = date(2026, 9, 7)


def _payload(dates: Iterable[date], *, bom: bool = False, crlf: bool = False) -> bytes:
    """CSV with the DATA_CONTRACT header and one row per date (MM/DD/YYYY in the Date column)."""
    rows = [HEADER] + [f"GOLDM ,{d:%m/%d/%Y},05OCT2026,1,1,1,1,1,1" for d in dates]
    text = ("\r\n" if crlf else "\n").join(rows) + ("\r\n" if crlf else "\n")
    return ("\ufeff" if bom else "").encode("utf-8") + text.encode("utf-8")


def _cfg(tmp_path: Path, **overrides: object) -> DataSourceConfig:
    base: dict[str, object] = {
        "url_template": "https://example.invalid/bhavcopy",
        "method": "GET",
        "params_template": {"Date": "{date}"},
        "headers": {"User-Agent": "test"},
        "request_date_format": "%d/%m/%Y",
        "timeout_s": 5,
        "min_interval_s": 0,
        "max_retries": 2,
        "backoff_base_s": 2.0,
        "raw_dir": str(tmp_path / "raw"),
    }
    base.update(overrides)
    return DataSourceConfig.model_validate(base)


class FakeTransport:
    """Returns queued responses in order; a ``TransportError`` instance is raised instead."""

    def __init__(self, *responses: bytes | TransportError) -> None:
        self.responses = list(responses)
        self.calls: list[BhavcopyRequest] = []

    def __call__(self, request: BhavcopyRequest) -> bytes:
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, TransportError):
            raise response
        return response


def _no_sleep(_: float) -> None:
    return None


# ------------------------------------------------------------------ request building


def test_build_request_uses_day_first_date(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, url_template="https://example.invalid/b/{date}")
    request = build_request(FRI, cfg)
    assert request.url == "https://example.invalid/b/04/09/2026"
    assert request.params == {"Date": "04/09/2026"}
    assert request.headers == {"User-Agent": "test"}
    assert request.method == "GET"


def test_build_request_refuses_unconfigured_endpoint(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, url_template=None)
    with pytest.raises(ConfigError, match="url_template"):
        build_request(FRI, cfg)


# ------------------------------------------------------------------ returned_trade_dates


def test_returned_dates_handles_bom_and_crlf() -> None:
    assert returned_trade_dates(_payload([FRI, FRI], bom=True, crlf=True)) == {FRI}


def test_returned_dates_empty_body() -> None:
    assert returned_trade_dates(b"") == set()
    assert returned_trade_dates(HEADER.encode()) == set()


def test_returned_dates_missing_date_column_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="no 'Date' column"):
        returned_trade_dates(b"Symbol,Close\nGOLDM,1\n")


def test_returned_dates_html_error_page_raises() -> None:
    with pytest.raises(BhavcopyFormatError):
        returned_trade_dates(b"<html><body>Service unavailable</body></html>")


# ------------------------------------------------------------------ happy path + cache


def test_fetch_saves_raw_bytes_untouched(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    payload = _payload([FRI, FRI], bom=True, crlf=True)
    transport = FakeTransport(payload)
    log = DQLog()

    result = fetch_bhavcopy(FRI, cfg, transport=transport, dq_log=log, sleep=_no_sleep)

    assert result.status == "fetched"
    assert result.path == raw_path(cfg.raw_dir_abs, FRI)
    assert result.path is not None and result.path.name == "bhavcopy_2026-09-04.csv"
    assert result.path.read_bytes() == payload  # byte-identical, BOM and CRLF preserved
    assert len(log) == 0
    assert transport.calls[0].params == {"Date": "04/09/2026"}


def test_fetch_is_idempotent_uses_cache(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    transport = FakeTransport(_payload([FRI]))
    fetch_bhavcopy(FRI, cfg, transport=transport, sleep=_no_sleep)

    again = fetch_bhavcopy(FRI, cfg, transport=transport, sleep=_no_sleep)

    assert again.status == "cached"
    assert len(transport.calls) == 1  # no second request


# ------------------------------------------------------------------ date validation


def test_holiday_substitution_is_discarded(tmp_path: Path) -> None:
    """A holiday request that returns the previous day's data must not be stored as the holiday."""
    cfg = _cfg(tmp_path)
    log = DQLog()
    transport = FakeTransport(_payload([THU]))

    result = fetch_bhavcopy(FRI, cfg, transport=transport, dq_log=log, sleep=_no_sleep)

    assert result.status == "discarded"
    assert not raw_path(cfg.raw_dir_abs, FRI).exists()
    assert not raw_path(cfg.raw_dir_abs, THU).exists()  # never re-labelled either
    assert result.path is not None
    assert result.path.parent.name == "rejected"
    assert result.path.name == "bhavcopy_req-2026-09-04_got-2026-09-03.csv"
    assert result.path.read_bytes() == _payload([THU])
    assert log.codes() == ["date_mismatch"]
    assert log.events[0].trade_date == FRI
    assert "2026-09-03" in log.events[0].message


def test_mixed_dates_in_response_is_discarded(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    log = DQLog()
    result = fetch_bhavcopy(
        FRI, cfg, transport=FakeTransport(_payload([FRI, THU])), dq_log=log, sleep=_no_sleep
    )
    assert result.status == "discarded"
    assert log.codes() == ["date_mismatch"]


def test_empty_response_is_discarded(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    log = DQLog()
    result = fetch_bhavcopy(
        FRI, cfg, transport=FakeTransport(_payload([])), dq_log=log, sleep=_no_sleep
    )
    assert result.status == "discarded"
    assert not raw_path(cfg.raw_dir_abs, FRI).exists()
    assert log.codes() == ["empty_response"]


def test_format_change_raises_and_saves_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(BhavcopyFormatError):
        fetch_bhavcopy(
            FRI, cfg, transport=FakeTransport(b"Symbol,Close\nGOLDM,1\n"), sleep=_no_sleep
        )
    assert not cfg.raw_dir_abs.exists()


# ------------------------------------------------------------------ retries + politeness


def test_retries_with_backoff_then_succeeds(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, max_retries=2, backoff_base_s=2.0)
    transport = FakeTransport(TransportError("503"), TransportError("timeout"), _payload([FRI]))
    sleeps: list[float] = []

    result = fetch_bhavcopy(FRI, cfg, transport=transport, sleep=sleeps.append)

    assert result.status == "fetched"
    assert len(transport.calls) == 3
    assert sleeps == [1.0, 2.0]  # 2**0, 2**1


def test_gives_up_after_max_retries(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, max_retries=1)
    transport = FakeTransport(TransportError("a"), TransportError("b"), _payload([FRI]))
    with pytest.raises(TransportError, match="giving up after 2 attempts"):
        fetch_bhavcopy(FRI, cfg, transport=transport, sleep=_no_sleep)
    assert len(transport.calls) == 2
    assert not cfg.raw_dir_abs.exists()


def test_rate_limiter_enforces_min_interval() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(1.0, clock=clock, sleep=sleep)
    limiter.wait()  # first call never sleeps
    now[0] += 0.25
    limiter.wait()  # 0.25 s elapsed -> sleep 0.75 s
    now[0] += 3.0
    limiter.wait()  # plenty elapsed -> no sleep
    assert sleeps == [0.75]


# ------------------------------------------------------------------ fetch_range


def test_fetch_range_skips_weekends_and_shares_log(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    # Thu 3, Fri 4, (Sat 5, Sun 6 skipped), Mon 7. Monday is a "holiday" returning Friday's data.
    transport = FakeTransport(_payload([THU]), _payload([FRI]), _payload([FRI]))
    log = DQLog()

    results = fetch_range(THU, MON, cfg, transport=transport, dq_log=log, sleep=_no_sleep)

    assert [r.trade_date for r in results] == [THU, FRI, MON]
    assert [r.status for r in results] == ["fetched", "fetched", "discarded"]
    assert len(transport.calls) == 3
    assert log.codes() == ["date_mismatch"]
    assert sorted(p.name for p in cfg.raw_dir_abs.glob("*.csv")) == [
        "bhavcopy_2026-09-03.csv",
        "bhavcopy_2026-09-04.csv",
    ]


def test_fetch_range_rejects_reversed_dates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="before start"):
        fetch_range(FRI, THU, _cfg(tmp_path), transport=FakeTransport(), sleep=_no_sleep)
