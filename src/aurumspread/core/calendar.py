"""Calendar helpers: days-to-expiry and the observed trading calendar.

Days-to-expiry is measured in *calendar* days because carry is quoted per
calendar day (PRD 6.2). The trading calendar is whatever dates actually appear
in the validated data; no holiday list is assumed.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import date

import pandas as pd

from aurumspread.config import ContractSpec

DTE_COL = "dte_days"


def days_to_expiry(trade_date: date, expiry_date: date) -> int:
    """Calendar days from ``trade_date`` to ``expiry_date`` (0 on expiry day, negative after)."""
    return (expiry_date - trade_date).days


def add_days_to_expiry(frame: pd.DataFrame, *, out_col: str = DTE_COL) -> pd.DataFrame:
    """Vectorised :func:`days_to_expiry` on ``trade_date`` / ``expiry_date`` columns."""
    out = frame.copy()
    expiry = pd.to_datetime(out["expiry_date"])
    trade = pd.to_datetime(out["trade_date"])
    out[out_col] = (expiry - trade).dt.days.astype("int64")
    return out


def expiry_in_window(expiry_date: date, spec: ContractSpec) -> bool:
    """Does the observed expiry day-of-month fall inside the configured window?"""
    lo, hi = spec.expiry_window_days
    return lo <= expiry_date.day <= hi


class TradingCalendar:
    """Sorted distinct trading dates observed in the data (from the registry)."""

    def __init__(self, dates: Iterable[date]) -> None:
        self._dates: list[date] = sorted(set(dates))
        if not self._dates:
            raise ValueError("trading calendar needs at least one date")

    @property
    def dates(self) -> list[date]:
        return list(self._dates)

    def __len__(self) -> int:
        return len(self._dates)

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and self._index(d) is not None

    def _index(self, d: date) -> int | None:
        i = bisect.bisect_left(self._dates, d)
        return i if i < len(self._dates) and self._dates[i] == d else None

    def next_trading_day(self, d: date, offset: int = 1) -> date | None:
        """The ``offset``-th trading day strictly after ``d`` (None if beyond the data)."""
        if offset < 1:
            raise ValueError("offset must be >= 1")
        i = bisect.bisect_right(self._dates, d) + offset - 1
        return self._dates[i] if i < len(self._dates) else None

    def prev_trading_day(self, d: date, offset: int = 1) -> date | None:
        """The ``offset``-th trading day strictly before ``d`` (None if before the data)."""
        if offset < 1:
            raise ValueError("offset must be >= 1")
        i = bisect.bisect_left(self._dates, d) - offset
        return self._dates[i] if i >= 0 else None

    def trading_days_between(self, start: date, end: date) -> int:
        """Number of trading days in ``(start, end]``; negative if ``end < start``."""
        return bisect.bisect_right(self._dates, end) - bisect.bisect_right(self._dates, start)

    def window(self, start: date, end: date) -> list[date]:
        """Trading days in ``[start, end]``."""
        lo = bisect.bisect_left(self._dates, start)
        hi = bisect.bisect_right(self._dates, end)
        return self._dates[lo:hi]
