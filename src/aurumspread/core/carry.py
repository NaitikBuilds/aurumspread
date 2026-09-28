"""Expiry alignment and carry adjustment (PRD 6.2; acceptance test 7).

GOLDM expires on the 3rd-5th, the other families on the 27th-31st, so same-named
months differ by ~25-30 days of carry. Before two families are compared:

1. list live contracts per family with days-to-expiry (``dte_days``);
2. estimate each family's implied carry (INR/g per calendar day) from the slope
   of its *own* curve (adjacent live expiries);
3. pair the two families' contracts by nearest DTE and shift leg B along its
   curve by ``carry_b * (dte_a - dte_b)`` so both legs refer to the same date;
4. spread = P_a - adjusted P_b, in INR/g.

If no carry estimate exists for leg B (single live expiry and no pooled
fallback) the spread is NaN: a naive cross-expiry comparison is never emitted.
Everything uses same-day data only, so there is no look-ahead by construction.
Constant-maturity prices are for analytics; trading uses real (symbol, expiry).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from aurumspread.config import AlignmentConfig
from aurumspread.core.calendar import DTE_COL
from aurumspread.core.lifecycle import STATUS_COL
from aurumspread.core.normalize import PURE_PRICE_COL

CARRY_COL = "carry_inr_per_g_per_day"
SPREAD_COL = "spread_inr_per_g"

PAIR_COLUMNS = [
    "trade_date",
    "symbol_a",
    "expiry_a",
    "dte_a",
    "price_a_inr_per_g",
    "symbol_b",
    "expiry_b",
    "dte_b",
    "price_b_inr_per_g",
    "dte_gap_days",
    "carry_b_inr_per_g_per_day",
    "carry_source",
    "adj_price_b_inr_per_g",
    "naive_spread_inr_per_g",
    SPREAD_COL,
]


def family_curve(day_frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Live contracts of one family on one day, ascending by DTE (columns: expiry, dte, price)."""
    mask = (day_frame["symbol"] == symbol) & (day_frame[STATUS_COL] == "live")
    curve = day_frame.loc[mask, ["expiry_date", DTE_COL, PURE_PRICE_COL]]
    curve = curve.sort_values(DTE_COL, kind="stable").reset_index(drop=True)
    if curve[DTE_COL].duplicated().any():
        raise ValueError(f"{symbol}: two live contracts share the same DTE on one day")
    return curve


def implied_carry(curve: pd.DataFrame, method: str) -> float:
    """Slope of a family's curve in INR/g per calendar day; NaN if fewer than two points."""
    if len(curve) < 2:
        return math.nan
    dte = curve[DTE_COL].to_numpy(dtype="float64")
    price = curve[PURE_PRICE_COL].to_numpy(dtype="float64")
    slopes = np.diff(price) / np.diff(dte)
    if method == "front_pair":
        return float(slopes[0])
    if method == "adjacent_median":
        return float(np.median(slopes))
    raise ValueError(f"unknown carry_method {method!r}")


def daily_carry_table(day_frame: pd.DataFrame, cfg: AlignmentConfig) -> pd.DataFrame:
    """Per-family carry for one day plus ``carry_source`` (own | pooled | none)."""
    symbols = sorted(day_frame["symbol"].unique().tolist())
    own = {s: implied_carry(family_curve(day_frame, s), cfg.carry_method) for s in symbols}
    finite = [c for c in own.values() if not math.isnan(c)]
    pooled = float(np.median(finite)) if (finite and cfg.pooled_carry_fallback) else math.nan
    rows = []
    for symbol in symbols:
        carry = own[symbol]
        if not math.isnan(carry):
            source = "own"
        elif not math.isnan(pooled):
            carry, source = pooled, "pooled"
        else:
            source = "none"
        rows.append({"symbol": symbol, CARRY_COL: carry, "carry_source": source})
    return pd.DataFrame(rows, columns=["symbol", CARRY_COL, "carry_source"])


@dataclass(frozen=True)
class PairChoice:
    expiry_a: date
    dte_a: int
    price_a: float
    expiry_b: date
    dte_b: int
    price_b: float


def choose_nearest_dte_pair(
    curve_a: pd.DataFrame, curve_b: pd.DataFrame, max_dte_gap_days: int
) -> PairChoice | None:
    """Pick the (a, b) contracts minimising |dte_a - dte_b|; ties go to the front."""
    best: PairChoice | None = None
    best_key: tuple[int, int] | None = None
    for a in curve_a.itertuples(index=False):
        for b in curve_b.itertuples(index=False):
            dte_a, dte_b = int(a[1]), int(b[1])
            gap = abs(dte_a - dte_b)
            if gap > max_dte_gap_days:
                continue
            key = (gap, dte_a + dte_b)
            if best_key is None or key < best_key:
                best_key = key
                best = PairChoice(a[0], dte_a, float(a[2]), b[0], dte_b, float(b[2]))
    return best


def align_pair_on_day(
    day_frame: pd.DataFrame,
    symbol_a: str,
    symbol_b: str,
    cfg: AlignmentConfig,
) -> dict[str, object] | None:
    """One :data:`PAIR_COLUMNS` record for ``trade_date`` or None if no pair can be formed."""
    curve_a = family_curve(day_frame, symbol_a)
    curve_b = family_curve(day_frame, symbol_b)
    if curve_a.empty or curve_b.empty:
        return None
    choice = choose_nearest_dte_pair(curve_a, curve_b, cfg.max_dte_gap_days)
    if choice is None:
        return None
    carry_table = daily_carry_table(day_frame, cfg).set_index("symbol")
    carry_b = float(carry_table.loc[symbol_b, CARRY_COL])
    source = str(carry_table.loc[symbol_b, "carry_source"])
    gap = choice.dte_a - choice.dte_b
    naive = choice.price_a - choice.price_b
    if math.isnan(carry_b):
        adj_b = math.nan
        spread = math.nan
    else:
        adj_b = choice.price_b + carry_b * gap
        spread = choice.price_a - adj_b
    trade_date = day_frame["trade_date"].iloc[0]
    return {
        "trade_date": trade_date,
        "symbol_a": symbol_a,
        "expiry_a": choice.expiry_a,
        "dte_a": choice.dte_a,
        "price_a_inr_per_g": choice.price_a,
        "symbol_b": symbol_b,
        "expiry_b": choice.expiry_b,
        "dte_b": choice.dte_b,
        "price_b_inr_per_g": choice.price_b,
        "dte_gap_days": gap,
        "carry_b_inr_per_g_per_day": carry_b,
        "carry_source": source,
        "adj_price_b_inr_per_g": adj_b,
        "naive_spread_inr_per_g": naive,
        SPREAD_COL: spread,
    }


def build_pair_series(
    prices: pd.DataFrame,
    symbol_a: str,
    symbol_b: str,
    cfg: AlignmentConfig,
) -> pd.DataFrame:
    """Aligned spread for every trade date in ``prices`` (needs lifecycle + normalize columns).

    ``prices`` must carry ``trade_date, symbol, expiry_date, dte_days, status,
    pure_price_inr_per_g``; only ``status == "live"`` rows are used. Each row of
    the result is computed from that day's data alone.
    """
    required = {"trade_date", "symbol", "expiry_date", DTE_COL, STATUS_COL, PURE_PRICE_COL}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise KeyError(f"prices frame lacks columns {missing}; run lifecycle + normalize first")
    records = []
    for _, day_frame in prices.groupby("trade_date", sort=True):
        record = align_pair_on_day(day_frame, symbol_a, symbol_b, cfg)
        if record is not None:
            records.append(record)
    return pd.DataFrame(records, columns=PAIR_COLUMNS)


def constant_maturity_price(curve: pd.DataFrame, target_dte_days: int) -> tuple[float, bool]:
    """Linear interpolation of a family's curve at ``target_dte_days``.

    Returns ``(price, extrapolated)``. Outside the observed DTE range the nearest
    segment's slope is extended and ``extrapolated`` is True; with a single point
    the price is returned flat (also flagged). Empty curve -> (NaN, True).
    """
    if curve.empty:
        return math.nan, True
    dte = curve[DTE_COL].to_numpy(dtype="float64")
    price = curve[PURE_PRICE_COL].to_numpy(dtype="float64")
    if len(curve) == 1:
        return float(price[0]), True
    t = float(target_dte_days)
    if dte[0] <= t <= dte[-1]:
        return float(np.interp(t, dte, price)), False
    if t < dte[0]:
        slope = (price[1] - price[0]) / (dte[1] - dte[0])
        return float(price[0] + slope * (t - dte[0])), True
    slope = (price[-1] - price[-2]) / (dte[-1] - dte[-2])
    return float(price[-1] + slope * (t - dte[-1])), True


def constant_maturity_table(prices: pd.DataFrame, cfg: AlignmentConfig) -> pd.DataFrame:
    """Constant-maturity INR/g per (trade_date, symbol, grid DTE); analytics only."""
    rows = []
    for trade_date, day_frame in prices.groupby("trade_date", sort=True):
        for symbol in sorted(day_frame["symbol"].unique().tolist()):
            curve = family_curve(day_frame, symbol)
            for target in cfg.constant_maturity_grid_days:
                value, extrapolated = constant_maturity_price(curve, target)
                rows.append(
                    {
                        "trade_date": trade_date,
                        "symbol": symbol,
                        "target_dte_days": target,
                        "cm_price_inr_per_g": value,
                        "extrapolated": extrapolated,
                        "n_points": len(curve),
                    }
                )
    return pd.DataFrame(
        rows,
        columns=[
            "trade_date",
            "symbol",
            "target_dte_days",
            "cm_price_inr_per_g",
            "extrapolated",
            "n_points",
        ],
    )
