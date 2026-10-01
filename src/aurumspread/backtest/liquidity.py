"""Liquidity filter and participation cap (docs/COST_MODEL.md item 1, BACKTEST_PROTOCOL rule 4).

``min_volume_lots`` and ``min_open_interest_lots`` are compared to the raw
``volume`` and ``open_interest`` columns. docs/DATA_CONTRACT.md has not
confirmed whether those columns are lots or units, so this module does not
convert them. A null floor is off, not zero. A non-positive raw value still
fails: there is no book to trade against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from aurumspread.config import LiquidityConfig


@dataclass(frozen=True)
class LiquidityDecision:
    """One contract-day. ``floors_active`` is false while both minima are null."""

    passed: bool
    reasons: tuple[str, ...]
    floors_active: bool


def passes_liquidity(
    volume: float,
    open_interest: float,
    liquidity: LiquidityConfig,
) -> LiquidityDecision:
    """Decide whether one contract-day clears the configured floors."""
    floors_active = (
        liquidity.min_volume_lots is not None or liquidity.min_open_interest_lots is not None
    )
    reasons: list[str] = []
    if not _positive(volume):
        reasons.append("volume_not_positive")
    elif liquidity.min_volume_lots is not None and volume < liquidity.min_volume_lots:
        reasons.append("below_min_volume")
    if not _positive(open_interest):
        reasons.append("open_interest_not_positive")
    elif (
        liquidity.min_open_interest_lots is not None
        and open_interest < liquidity.min_open_interest_lots
    ):
        reasons.append("below_min_open_interest")
    return LiquidityDecision(
        passed=not reasons,
        reasons=tuple(reasons),
        floors_active=floors_active,
    )


def filter_liquid(frame: pd.DataFrame, liquidity: LiquidityConfig) -> pd.DataFrame:
    """Copy of ``frame`` keeping rows that :func:`passes_liquidity` accepts.

    Requires ``volume`` and ``open_interest``. The input is not mutated.
    """
    missing = [column for column in ("volume", "open_interest") if column not in frame.columns]
    if missing:
        raise KeyError(f"frame lacks columns {missing}")
    if frame.empty:
        return frame.copy()
    keep: list[bool] = []
    for volume, open_interest in zip(frame["volume"], frame["open_interest"], strict=True):
        decision = passes_liquidity(_as_float(volume), _as_float(open_interest), liquidity)
        keep.append(decision.passed)
    mask = pd.Series(keep, index=frame.index)
    return frame.loc[mask].reset_index(drop=True)


def cap_to_participation(
    order_qty: float, market_qty: float, max_participation_pct: float
) -> float:
    """Scale ``order_qty`` so it does not exceed ``max_participation_pct`` of ``market_qty``.

    Both quantities must already be in the same unit. A non-positive market
    quantity returns 0. This does not interpret Bhavcopy volume as lots.
    """
    if isinstance(max_participation_pct, bool) or not isinstance(
        max_participation_pct, (int, float)
    ):
        raise TypeError(f"max_participation_pct must be a number, got {max_participation_pct!r}")
    if not 0 < max_participation_pct <= 100:
        raise ValueError(f"max_participation_pct must be in (0, 100], got {max_participation_pct}")
    if order_qty < 0:
        raise ValueError(f"order_qty must be >= 0, got {order_qty}")
    if not _positive(market_qty):
        return 0.0
    cap = market_qty * max_participation_pct / 100.0
    return float(min(order_qty, cap))


def _positive(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _as_float(value: object) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    try:
        if pd.isna(value):
            return math.nan
    except TypeError:
        pass
    return float(value)  # type: ignore[arg-type]
