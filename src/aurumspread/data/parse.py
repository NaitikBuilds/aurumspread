"""Bhavcopy field parsers (docs/DATA_CONTRACT.md "Parsing rules").

Pure functions, no pandas here: they are used both by the fetcher (to check the
returned ``Date``) and by the frame parser. Every malformed value raises
:class:`BhavcopyFormatError` instead of being silently coerced.
"""

from __future__ import annotations

import re
from datetime import date, datetime

RESPONSE_DATE_FORMAT = "%m/%d/%Y"  # Date column in the response, e.g. 09/04/2026

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
