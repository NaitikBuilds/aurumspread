"""Bhavcopy fetch + cache + date validation (T02; docs/DATA_CONTRACT.md, rules/40-data-layer).

Guarantees:
* Raw responses are written byte-for-byte unmodified under ``raw_dir`` and never
  overwritten (idempotent: a cached day is not re-requested).
* The returned ``Date`` column is compared with the requested date. On mismatch
  (holiday substitution, future date, malformed payload) the payload is NOT
  stored under the requested date; it goes to ``raw_dir/rejected/`` for audit and
  a ``date_mismatch`` DQ event is logged.
* Politeness: rate limiter (``min_interval_s``) and bounded retries with backoff.
* The transport is injectable so tests never touch the network.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import requests

from aurumspread.config import ConfigError, DataSourceConfig
from aurumspread.data.dq import DQLog
from aurumspread.data.parse import BhavcopyFormatError, format_request_date, parse_response_date

DATE_COLUMN = "Date"
RAW_FILE_PATTERN = "bhavcopy_{trade_date:%Y-%m-%d}.csv"


class TransportError(RuntimeError):
    """Network-level failure (HTTP error, timeout). Retried up to ``max_retries``."""


@dataclass(frozen=True)
class BhavcopyRequest:
    method: Literal["GET", "POST"]
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout_s: float


Transport = Callable[[BhavcopyRequest], bytes]


@dataclass(frozen=True)
class FetchResult:
    trade_date: date
    status: Literal["cached", "fetched", "discarded"]
    path: Path | None
    detail: str = ""


def build_request(trade_date: date, cfg: DataSourceConfig) -> BhavcopyRequest:
    """Substitute the DD/MM/YYYY request date into the configured URL/params."""
    if cfg.url_template is None:
        raise ConfigError(
            "data_source.url_template is null: verify the endpoint (docs/DATA_CONTRACT.md "
            "'Open questions') and fill config/data_source.yaml before fetching"
        )
    date_text = format_request_date(trade_date, cfg.request_date_format)
    return BhavcopyRequest(
        method=cfg.method,
        url=cfg.url_template.format(date=date_text),
        params={k: v.format(date=date_text) for k, v in cfg.params_template.items()},
        headers=dict(cfg.headers),
        timeout_s=cfg.timeout_s,
    )


def requests_transport(request: BhavcopyRequest) -> bytes:
    """Default transport: GET sends params in the query string, POST as a form body."""
    kwargs = {"params": request.params} if request.method == "GET" else {"data": request.params}
    try:
        response = requests.request(
            request.method,
            request.url,
            headers=request.headers,
            timeout=request.timeout_s,
            **kwargs,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TransportError(str(exc)) from exc
    return response.content


class RateLimiter:
    """Ensure at least ``min_interval_s`` between consecutive :meth:`wait` returns."""

    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._min_interval_s - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def returned_trade_dates(payload: bytes) -> set[date]:
    """Distinct ``Date`` values in a CSV payload. Raises on non-CSV or missing column."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BhavcopyFormatError("response is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return set()
    by_name = {name.strip(): name for name in reader.fieldnames}
    if DATE_COLUMN not in by_name:
        raise BhavcopyFormatError(
            f"response has no {DATE_COLUMN!r} column; columns: {sorted(by_name)}"
        )
    column = by_name[DATE_COLUMN]
    return {parse_response_date(row[column] or "") for row in reader}


def raw_path(raw_dir: Path, trade_date: date) -> Path:
    return raw_dir / RAW_FILE_PATTERN.format(trade_date=trade_date)


def rejected_path(raw_dir: Path, requested: date, returned: set[date]) -> Path:
    got = "_".join(d.isoformat() for d in sorted(returned)) or "none"
    return raw_dir / "rejected" / f"bhavcopy_req-{requested.isoformat()}_got-{got}.csv"


def _fetch_with_retries(
    request: BhavcopyRequest,
    cfg: DataSourceConfig,
    transport: Transport,
    rate_limiter: RateLimiter,
    sleep: Callable[[float], None],
) -> bytes:
    last_exc: TransportError | None = None
    for attempt in range(cfg.max_retries + 1):
        rate_limiter.wait()
        try:
            return transport(request)
        except TransportError as exc:
            last_exc = exc
            if attempt < cfg.max_retries:
                sleep(cfg.backoff_base_s**attempt)
    raise TransportError(
        f"{request.url}: giving up after {cfg.max_retries + 1} attempts: {last_exc}"
    ) from last_exc


def fetch_bhavcopy(
    trade_date: date,
    cfg: DataSourceConfig,
    *,
    transport: Transport = requests_transport,
    dq_log: DQLog | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    raw_dir: Path | None = None,
) -> FetchResult:
    """Fetch one trading day into ``raw_dir``; see module docstring for guarantees."""
    dq_log = dq_log if dq_log is not None else DQLog()
    raw_dir = raw_dir if raw_dir is not None else cfg.raw_dir_abs
    rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter(cfg.min_interval_s)

    target = raw_path(raw_dir, trade_date)
    if target.exists():
        return FetchResult(trade_date, "cached", target)

    request = build_request(trade_date, cfg)
    payload = _fetch_with_retries(request, cfg, transport, rate_limiter, sleep)
    returned = returned_trade_dates(payload)

    if returned != {trade_date}:
        reject = rejected_path(raw_dir, trade_date, returned)
        reject.parent.mkdir(parents=True, exist_ok=True)
        reject.write_bytes(payload)
        code = "empty_response" if not returned else "date_mismatch"
        detail = (
            f"requested {trade_date.isoformat()} but response Date values were "
            f"{[d.isoformat() for d in sorted(returned)]}; saved to {reject}"
        )
        dq_log.add(trade_date, "fetch", code, detail)
        return FetchResult(trade_date, "discarded", reject, detail)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return FetchResult(trade_date, "fetched", target)


def fetch_range(
    start: date,
    end: date,
    cfg: DataSourceConfig,
    *,
    include_weekends: bool = False,
    transport: Transport = requests_transport,
    dq_log: DQLog | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    raw_dir: Path | None = None,
) -> list[FetchResult]:
    """Fetch every day in ``[start, end]`` (weekdays by default), sharing one rate limiter.

    Holidays are not special-cased: the endpoint's substituted date fails the
    date check and the day is discarded and logged.
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    dq_log = dq_log if dq_log is not None else DQLog()
    rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter(cfg.min_interval_s)
    results: list[FetchResult] = []
    current = start
    while current <= end:
        if include_weekends or current.weekday() < 5:
            results.append(
                fetch_bhavcopy(
                    current,
                    cfg,
                    transport=transport,
                    dq_log=dq_log,
                    rate_limiter=rate_limiter,
                    sleep=sleep,
                    raw_dir=raw_dir,
                )
            )
        current += timedelta(days=1)
    return results
