"""Tests for aurumspread.data.parse (DATA_CONTRACT.md parsing rules)."""

from __future__ import annotations

from datetime import date

import pytest

from aurumspread.data.dq import DQLog
from aurumspread.data.parse import (
    BhavcopyFormatError,
    format_request_date,
    normalize_symbol,
    parse_expiry_date,
    parse_response_date,
)

# ---------------------------------------------------------------- request date (DD/MM/YYYY)


def test_request_date_is_day_first() -> None:
    assert format_request_date(date(2026, 9, 4), "%d/%m/%Y") == "04/09/2026"


# ---------------------------------------------------------------- response date (MM/DD/YYYY)


def test_response_date_is_month_first() -> None:
    # 09/04/2026 in the response means 4 September 2026, not 9 April.
    assert parse_response_date("09/04/2026") == date(2026, 9, 4)


def test_response_date_tolerates_padding() -> None:
    assert parse_response_date("  12/31/2025 ") == date(2025, 12, 31)


@pytest.mark.parametrize("raw", ["31/12/2025", "2025-12-31", "", "abc", "13/01/2026"])
def test_response_date_rejects_other_formats(raw: str) -> None:
    with pytest.raises(BhavcopyFormatError, match="MM/DD/YYYY"):
        parse_response_date(raw)


def test_request_and_response_round_trip() -> None:
    d = date(2026, 1, 2)
    assert parse_response_date(d.strftime("%m/%d/%Y")) == d
    assert format_request_date(d, "%d/%m/%Y") == "02/01/2026"


# ---------------------------------------------------------------- expiry date (DDMONYYYY)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04SEP2026", date(2026, 9, 4)),
        ("31oct2025", date(2025, 10, 31)),
        (" 5JAN2027 ", date(2027, 1, 5)),
        ("29FEB2028", date(2028, 2, 29)),
    ],
)
def test_expiry_date_parses(raw: str, expected: date) -> None:
    assert parse_expiry_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "SEP2026", "04SEPT2026", "04-SEP-2026", "2026SEP04"])
def test_expiry_date_rejects_wrong_shape(raw: str) -> None:
    with pytest.raises(BhavcopyFormatError, match="DDMONYYYY"):
        parse_expiry_date(raw)


def test_expiry_date_rejects_unknown_month() -> None:
    with pytest.raises(BhavcopyFormatError, match="unknown month"):
        parse_expiry_date("04XYZ2026")


def test_expiry_date_rejects_impossible_day() -> None:
    with pytest.raises(BhavcopyFormatError, match="real calendar date"):
        parse_expiry_date("30FEB2026")


# ---------------------------------------------------------------- symbol


@pytest.mark.parametrize("raw", ["GOLDM", "  GOLDM", "goldm  ", "\tGOLDM\n"])
def test_symbol_is_stripped_and_uppercased(raw: str) -> None:
    assert normalize_symbol(raw) == "GOLDM"


def test_empty_symbol_rejected() -> None:
    with pytest.raises(BhavcopyFormatError, match="empty"):
        normalize_symbol("   ")


# ---------------------------------------------------------------- DQ log


def test_dq_log_collects_and_exports() -> None:
    log = DQLog()
    assert len(log) == 0
    assert list(log.to_frame().columns) == ["trade_date", "stage", "code", "message", "key"]
    log.add(date(2026, 9, 4), "fetch", "date_mismatch", "got 09/03/2026")
    log.add(None, "parse", "bad_expiry", "x", key="GOLDM/2026-10-05")
    assert log.codes() == ["date_mismatch", "bad_expiry"]
    frame = log.to_frame()
    assert len(frame) == 2
    assert frame.loc[1, "key"] == "GOLDM/2026-10-05"
