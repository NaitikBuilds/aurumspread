"""Term structure per family: calendar spreads and roll-down decomposition (PRD 6.5, 6.2).

* :func:`calendar_spread_series` - same-family spread between the two nearest
  live expiries (e.g. GOLDTEN Sep vs Oct), the implied carry in INR/g per
  calendar day and the annualised roll yield. Uses the actual DTE gap, never a
  nominal month.
* :func:`roll_down_table` - for each contract, split its own day-over-day price
  change into ``roll_down`` (what yesterday's curve implied for today's DTE,
  i.e. the effect of DTE shrinking under an unchanged curve) and
  ``curve_change`` (everything else: level and shape moves). The two components
  sum exactly to the total.

Same-day/previous-day data only; nothing here looks forward.
"""

from __future__ import annotations

import math

import pandas as pd

from aurumspread.core.calendar import DTE_COL
from aurumspread.core.carry import constant_maturity_price, family_curve
from aurumspread.core.normalize import PURE_PRICE_COL

DAYS_PER_YEAR = 365.0

CALENDAR_SPREAD_COLUMNS = [
    "trade_date",
    "symbol",
    "expiry_near",
    "dte_near",
    "price_near_inr_per_g",
    "expiry_far",
    "dte_far",
    "price_far_inr_per_g",
    "dte_gap_days",
    "calendar_spread_inr_per_g",
    "carry_inr_per_g_per_day",
    "roll_yield_pct_annualised",
]

ROLL_DOWN_COLUMNS = [
    "trade_date",
    "prev_trade_date",
    "symbol",
    "expiry_date",
    "dte_prev",
    "dte_now",
    "price_prev_inr_per_g",
    "price_now_inr_per_g",
    "cm_prev_at_dte_now_inr_per_g",
    "roll_down_inr_per_g",
    "curve_change_inr_per_g",
    "total_change_inr_per_g",
    "extrapolated",
]


def calendar_spread_on_day(day_frame: pd.DataFrame, symbol: str) -> dict[str, object] | None:
    """Front-two calendar spread for one family on one day; None with < 2 live expiries."""
    curve = family_curve(day_frame, symbol)
    if len(curve) < 2:
        return None
    near, far = curve.iloc[0], curve.iloc[1]
    dte_near, dte_far = int(near[DTE_COL]), int(far[DTE_COL])
    p_near, p_far = float(near[PURE_PRICE_COL]), float(far[PURE_PRICE_COL])
    gap = dte_far - dte_near
    spread = p_far - p_near
    carry = spread / gap
    return {
        "trade_date": day_frame["trade_date"].iloc[0],
        "symbol": symbol,
        "expiry_near": near["expiry_date"],
        "dte_near": dte_near,
        "price_near_inr_per_g": p_near,
        "expiry_far": far["expiry_date"],
        "dte_far": dte_far,
        "price_far_inr_per_g": p_far,
        "dte_gap_days": gap,
        "calendar_spread_inr_per_g": spread,
        "carry_inr_per_g_per_day": carry,
        "roll_yield_pct_annualised": carry * DAYS_PER_YEAR / p_near * 100.0,
    }


def calendar_spread_series(prepared: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """:func:`calendar_spread_on_day` for every trade date (needs lifecycle + normalize columns)."""
    records = []
    for _, day_frame in prepared.groupby("trade_date", sort=True):
        record = calendar_spread_on_day(day_frame, symbol)
        if record is not None:
            records.append(record)
    return pd.DataFrame(records, columns=CALENDAR_SPREAD_COLUMNS)


def roll_down_table(prepared: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Roll-down vs curve-change decomposition for every contract of ``symbol``.

    For consecutive trading days (t_prev, t) and each contract live on both:

        cm_prev      = yesterday's curve interpolated at today's DTE
        roll_down    = cm_prev - P_prev
        curve_change = P_now - cm_prev
        total        = P_now - P_prev  (== roll_down + curve_change)

    ``extrapolated`` is True when today's DTE lies outside yesterday's curve
    (always the case for the front contract), so its roll-down is an extension
    of the nearest segment's slope.
    """
    family = prepared[prepared["symbol"] == symbol]
    days = sorted(family["trade_date"].unique().tolist())
    records = []
    for prev_day, day in zip(days[:-1], days[1:], strict=True):
        prev_frame = family[family["trade_date"] == prev_day]
        now_frame = family[family["trade_date"] == day]
        curve_prev = family_curve(prev_frame, symbol)
        curve_now = family_curve(now_frame, symbol)
        if curve_prev.empty or curve_now.empty:
            continue
        prev_by_expiry = curve_prev.set_index("expiry_date")
        for now in curve_now.itertuples(index=False):
            expiry, dte_now, p_now = now[0], int(now[1]), float(now[2])
            if expiry not in prev_by_expiry.index:
                continue
            dte_prev = int(prev_by_expiry.loc[expiry, DTE_COL])
            p_prev = float(prev_by_expiry.loc[expiry, PURE_PRICE_COL])
            cm_prev, extrapolated = constant_maturity_price(curve_prev, dte_now)
            if math.isnan(cm_prev):
                continue
            roll_down = cm_prev - p_prev
            records.append(
                {
                    "trade_date": day,
                    "prev_trade_date": prev_day,
                    "symbol": symbol,
                    "expiry_date": expiry,
                    "dte_prev": dte_prev,
                    "dte_now": dte_now,
                    "price_prev_inr_per_g": p_prev,
                    "price_now_inr_per_g": p_now,
                    "cm_prev_at_dte_now_inr_per_g": cm_prev,
                    "roll_down_inr_per_g": roll_down,
                    "curve_change_inr_per_g": p_now - cm_prev,
                    "total_change_inr_per_g": p_now - p_prev,
                    "extrapolated": extrapolated,
                }
            )
    return pd.DataFrame(records, columns=ROLL_DOWN_COLUMNS)
