"""Tests for aurumspread.data.parse (DATA_CONTRACT.md parsing rules)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from aurumspread.data.dq import DQLog
from aurumspread.data.parse import (
    PARSED_COLUMNS,
    BhavcopyFormatError,
    format_request_date,
    normalize_symbol,
    parse_bhavcopy_csv,
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


# ---------------------------------------------------------------- whole-file parser
#
# Rows below follow the DATA_CONTRACT.md formats exactly (padded symbol, MM/DD/YYYY
# Date, DDMONYYYY expiry). They exercise parsing mechanics only and are not market
# data; add fixture-based tests from real Bhavcopy rows under tests/fixtures/.

HEADER = "Symbol,Date,ExpiryDate,Open,High,Low,Close,Volume,OpenInterest"


def _csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def test_parse_csv_types_and_columns() -> None:
    frame = parse_bhavcopy_csv(
        _csv(
            "GOLDM    ,09/04/2026,05OCT2026,100000,100500,99800,100200,1500,3200",
            " goldpetal,09/04/2026,30SEP2026,10050,10060,10040,10055,,",
        ),
        source_name="unit-test",
    )
    assert list(frame.columns) == [*PARSED_COLUMNS, "source", "source_row"]
    assert frame["source_row"].tolist() == [2, 3]  # line numbers in the raw file
    assert frame["symbol"].tolist() == ["GOLDM", "GOLDPETAL"]
    assert frame["trade_date"].tolist() == [date(2026, 9, 4), date(2026, 9, 4)]
    assert frame["expiry_date"].tolist() == [date(2026, 10, 5), date(2026, 9, 30)]
    assert frame["close_inr"].tolist() == [100200.0, 10055.0]
    assert frame["close_inr"].dtype == np.float64
    # Blank Volume / OpenInterest are kept as NaN for the validator to flag as thin.
    assert frame.loc[0, "volume"] == 1500.0
    assert np.isnan(frame.loc[1, "volume"])
    assert np.isnan(frame.loc[1, "open_interest"])
    assert frame["source"].tolist() == ["unit-test", "unit-test"]


def test_parse_csv_from_path_with_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "bhavcopy_2026-09-04.csv"
    body = HEADER + "\r\n" + "GOLDTEN,09/04/2026,30SEP2026,1,1,1,1,1,1" + "\r\n"
    path.write_bytes("\ufeff".encode() + body.encode("utf-8"))
    frame = parse_bhavcopy_csv(path)
    assert len(frame) == 1
    assert frame.loc[0, "symbol"] == "GOLDTEN"
    assert frame.loc[0, "source"] == str(path)


def test_parse_csv_header_only_gives_empty_typed_frame() -> None:
    frame = parse_bhavcopy_csv(_csv())
    assert len(frame) == 0
    assert list(frame.columns) == [*PARSED_COLUMNS, "source", "source_row"]


def test_parse_csv_tolerates_padded_header_names() -> None:
    header = "Symbol, Date ,ExpiryDate,Open,High,Low,Close,Volume,OpenInterest"
    frame = parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,1,1,1", header=header))
    assert frame.loc[0, "trade_date"] == date(2026, 9, 4)


def test_parse_csv_empty_payload_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="empty"):
        parse_bhavcopy_csv(b"")


def test_parse_csv_missing_column_raises() -> None:
    header = "Symbol,Date,ExpiryDate,Open,High,Low,Close,Volume"  # no OpenInterest
    with pytest.raises(BhavcopyFormatError, match=r"missing=\['OpenInterest'\]"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,1,1", header=header))


def test_parse_csv_unknown_column_raises() -> None:
    header = HEADER + ",SettlementPrice"
    with pytest.raises(BhavcopyFormatError, match=r"unknown=\['SettlementPrice'\]"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,1,1,1,1", header=header))


def test_parse_csv_bad_date_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="MM/DD/YYYY"):
        parse_bhavcopy_csv(_csv("GOLDM,2026-09-04,05OCT2026,1,1,1,1,1,1"))


def test_parse_csv_bad_expiry_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="DDMONYYYY"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,2026-10-05,1,1,1,1,1,1"))


def test_parse_csv_non_numeric_price_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="Close: non-numeric"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,n/a,1,1"))


def test_parse_csv_blank_price_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="Close: blank"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,,1,1"))


def test_parse_csv_non_numeric_volume_raises() -> None:
    with pytest.raises(BhavcopyFormatError, match="Volume: non-numeric"):
        parse_bhavcopy_csv(_csv("GOLDM,09/04/2026,05OCT2026,1,1,1,1,many,1"))


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
