"""Bhavcopy parsers (docs/DATA_CONTRACT.md "Parsing rules").

Field-level parsers are pure functions used both by the fetcher (to check the
returned ``Date``) and by :func:`parse_bhavcopy_csv`, which turns one raw file
into a typed DataFrame. Every malformed value or unexpected column raises
:class:`BhavcopyFormatError` instead of being silently coerced.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

RESPONSE_DATE_FORMAT = "%m/%d/%Y"  # Date column in the response, e.g. 09/04/2026

# Raw header (DATA_CONTRACT.md "Fields provided") -> parsed column name with units.
# Prices are INR per quote unit of the contract (GOLDM: per 10 g), see config/contracts.yaml.
# TODO(verify): Volume / OpenInterest units (lots or units) are an open question in
# DATA_CONTRACT.md; the names stay unit-less until a human confirms.
RAW_TO_PARSED: dict[str, str] = {
    "Symbol": "symbol",
    "Date": "trade_date",
    "ExpiryDate": "expiry_date",
    "Open": "open_inr",
    "High": "high_inr",
    "Low": "low_inr",
    "Close": "close_inr",
    "Volume": "volume",
    "OpenInterest": "open_interest",
}
PRICE_COLUMNS = ("open_inr", "high_inr", "low_inr", "close_inr")
COUNT_COLUMNS = ("volume", "open_interest")
PARSED_COLUMNS = tuple(RAW_TO_PARSED.values())

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_EXPIRY_RE = re.compile(r"^(\d{1,2})([A-Z]{3})(\d{4})$")


class BhavcopyFormatError(ValueError):
    """A raw Bhavcopy value does not match docs/DATA_CONTRACT.md."""


def format_request_date(trade_date: date, fmt: str) -> str:
    """Render the request date, e.g. ``%d/%m/%Y`` -> ``04/09/2026`` (DD/MM/YYYY)."""
    return trade_date.strftime(fmt)


def parse_response_date(raw: str) -> date:
    """Parse the response ``Date`` column (MM/DD/YYYY, e.g. ``09/04/2026`` = 4 Sep 2026)."""
    text = raw.strip()
    try:
        return datetime.strptime(text, RESPONSE_DATE_FORMAT).date()
    except ValueError as exc:
        raise BhavcopyFormatError(f"Date {raw!r} is not MM/DD/YYYY") from exc


def parse_expiry_date(raw: str) -> date:
    """Parse ``ExpiryDate`` like ``04SEP2026`` (locale-independent)."""
    text = raw.strip().upper()
    match = _EXPIRY_RE.match(text)
    if match is None:
        raise BhavcopyFormatError(f"ExpiryDate {raw!r} is not DDMONYYYY")
    day, mon, year = match.groups()
    if mon not in _MONTHS:
        raise BhavcopyFormatError(f"ExpiryDate {raw!r} has unknown month {mon!r}")
    try:
        return date(int(year), _MONTHS[mon], int(day))
    except ValueError as exc:
        raise BhavcopyFormatError(f"ExpiryDate {raw!r} is not a real calendar date") from exc


def normalize_symbol(raw: str) -> str:
    """Strip padding and upper-case the ``Symbol`` column."""
    symbol = raw.strip().upper()
    if not symbol:
        raise BhavcopyFormatError("Symbol is empty")
    return symbol


# --------------------------------------------------------------------------- whole file


def _read_text(source: bytes | str | Path) -> str:
    if isinstance(source, Path):
        source = source.read_bytes()
    if isinstance(source, bytes):
        try:
            return source.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BhavcopyFormatError("payload is not UTF-8 text") from exc
    return source.lstrip("\ufeff")


def _check_header(columns: list[str]) -> None:
    stripped = [c.strip() for c in columns]
    expected = list(RAW_TO_PARSED)
    unknown = sorted(set(stripped) - set(expected))
    missing = [c for c in expected if c not in stripped]
    if unknown or missing:
        raise BhavcopyFormatError(
            f"header does not match DATA_CONTRACT.md: missing={missing} unknown={unknown}"
        )
    if len(stripped) != len(set(stripped)):
        raise BhavcopyFormatError(f"duplicate header columns: {stripped}")


def _parse_number(series: pd.Series, column: str, *, allow_blank: bool) -> pd.Series:
    text = series.str.strip()
    blank = text == ""
    if blank.any() and not allow_blank:
        raise BhavcopyFormatError(f"{column}: blank value in rows {list(text.index[blank])}")
    numeric = pd.to_numeric(text.mask(blank, None), errors="coerce")
    bad = numeric.isna() & ~blank
    if bad.any():
        examples = text[bad].head(3).tolist()
        raise BhavcopyFormatError(f"{column}: non-numeric values {examples}")
    return numeric.astype("float64")


def parse_bhavcopy_csv(source: bytes | str | Path, *, source_name: str = "") -> pd.DataFrame:
    """Parse one raw Bhavcopy CSV into a typed frame with :data:`PARSED_COLUMNS`.

    Rows are returned unfiltered and unvalidated (see ``validate.py``); only the
    *shape* is enforced here: exact header, parseable dates, numeric prices.
    Blank ``Volume`` / ``OpenInterest`` become NaN (DATA_CONTRACT rule 4 flags them).
    A ``source`` column records where each row came from (traceability).
    """
    text = _read_text(source)
    if not text.strip():
        raise BhavcopyFormatError("payload is empty")
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=False)
    _check_header(list(raw.columns))
    raw.columns = [c.strip() for c in raw.columns]

    out = pd.DataFrame(index=raw.index)
    out["symbol"] = raw["Symbol"].map(normalize_symbol)
    out["trade_date"] = raw["Date"].map(parse_response_date)
    out["expiry_date"] = raw["ExpiryDate"].map(parse_expiry_date)
    for raw_col in ("Open", "High", "Low", "Close"):
        out[RAW_TO_PARSED[raw_col]] = _parse_number(raw[raw_col], raw_col, allow_blank=False)
    for raw_col in ("Volume", "OpenInterest"):
        out[RAW_TO_PARSED[raw_col]] = _parse_number(raw[raw_col], raw_col, allow_blank=True)
    out["source"] = source_name or (str(source) if isinstance(source, Path) else "<bytes>")
    return out.reset_index(drop=True)
