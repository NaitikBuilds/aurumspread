"""Daily P&L attribution (PRD Section 8, task T10).

Decomposes daily strategy performance into:
- Beta: net unhedged gold exposure times reference gold move ((sum g_i) * dRef).
- Alpha: relative-value spread move sum g_i * (dP_i - dRef).
- Carry: roll-down / implied carry accrual (reported separately as a subset of alpha).
- Cost: signed drag of transaction fees and slippage (<= 0.0).
- Residual: difference between total P&L and components (0.0 under exact mark-to-market).

Mathematical identity:
    total_pnl_inr = beta_inr + alpha_inr + cost_inr + residual_inr
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd

from aurumspread.backtest.costs import order_cost_inr
from aurumspread.config import ContractsConfig, CostsConfig
from aurumspread.core.calendar import TradingCalendar

ATTRIBUTION_COLUMNS = (
    "trade_date",
    "beta_inr",
    "alpha_inr",
    "carry_inr",
    "cost_inr",
    "residual_inr",
    "total_pnl_inr",
)


def compute_daily_attribution(
    trades: pd.DataFrame | Any,
    signals: pd.DataFrame | None = None,
    *,
    d_ref: float | pd.Series | Mapping[date, float] | Callable[[date], float] | str | None = None,
    calendar: TradingCalendar | None = None,
    costs: CostsConfig | None = None,
    contracts: ContractsConfig | None = None,
    multiplier: float = 1.0,
    include_idle: bool = False,
) -> pd.DataFrame:
    """Compute daily attribution from closed trades and signal price series.

    Parameters
    ----------
    trades : pd.DataFrame or WalkForwardResult
        Trade log containing trade_id, symbol_a, expiry_a, symbol_b, expiry_b,
        entry_fill_date, exit_fill_date, qty_g_a, qty_g_b, residual_g,
        entry_fill_a_inr_per_g, entry_fill_b_inr_per_g, exit_fill_a_inr_per_g,
        exit_fill_b_inr_per_g, cost_inr, and side.
    signals : pd.DataFrame, optional
        Signal frame with trade_date, symbol_a, expiry_a, symbol_b, expiry_b,
        price_a_inr_per_g, price_b_inr_per_g, and optional carry_b_inr_per_g_per_day.
    d_ref : float, Series, Mapping, Callable, str, or None, default None
        Reference gold price change (INR/g) on each session. Defaults to 0.0
        (pure alpha view). A symbol name (e.g. "GOLDM") extracts daily reference
        returns from the signal frame.
    calendar : TradingCalendar, optional
        Trading calendar for determining sessions between entry and exit.
    costs : CostsConfig, optional
        Cost configuration for exact daily order fee splits.
    contracts : ContractsConfig, optional
        Contract specifications.
    multiplier : float, default 1.0
        Stress multiplier on fees and slippage.
    include_idle : bool, default False
        If True, includes calendar dates with zero P&L when no book was open.

    Returns
    -------
    pd.DataFrame
        Daily attribution table keyed by session trade_date.
    """
    if hasattr(trades, "attribution") and isinstance(trades.attribution, pd.DataFrame):
        if d_ref is None and costs is None and not include_idle:
            return trades.attribution

    trades_df = getattr(trades, "trades", trades)
    if not isinstance(trades_df, pd.DataFrame):
        raise TypeError(f"expected DataFrame or WalkForwardResult, got {type(trades).__name__}")

    if trades_df.empty:
        return _empty_attribution_frame()

    if signals is None:
        raise ValueError("signals frame is required to compute daily price movements")

    frame = signals.copy()
    frame["trade_date"] = frame["trade_date"].map(_as_date)
    for col in ("expiry_a", "expiry_b"):
        if col in frame.columns:
            frame[col] = frame[col].map(_as_date)

    if calendar is None:
        signal_dates = sorted(set(frame["trade_date"]))
        calendar = TradingCalendar(signal_dates)

    d_ref_fn = _build_d_ref_evaluator(d_ref, frame)

    signals_by_key: dict[tuple, pd.Series] = {
        (
            _as_date(row["trade_date"]),
            row["symbol_a"],
            _as_date(row["expiry_a"]),
            row["symbol_b"],
            _as_date(row["expiry_b"]),
        ): row
        for _, row in frame.iterrows()
    }

    # Aggregate daily attribution components
    daily_beta: dict[date, float] = defaultdict(float)
    daily_alpha: dict[date, float] = defaultdict(float)
    daily_carry: dict[date, float] = defaultdict(float)
    daily_cost: dict[date, float] = defaultdict(float)
    daily_total: dict[date, float] = defaultdict(float)

    active_dates: set[date] = set()

    for _, trade in trades_df.iterrows():
        entry_date = _as_date(trade["entry_fill_date"])
        exit_date = _as_date(trade["exit_fill_date"])
        pair = (
            trade["symbol_a"],
            _as_date(trade["expiry_a"]),
            trade["symbol_b"],
            _as_date(trade["expiry_b"]),
        )
        qty_a = float(trade["qty_g_a"])
        qty_b = float(trade["qty_g_b"])
        res_g = float(trade["residual_g"]) if pd.notna(trade["residual_g"]) else qty_a + qty_b
        side = str(trade["side"])
        entry_fill_a = float(trade["entry_fill_a_inr_per_g"])
        entry_fill_b = float(trade["entry_fill_b_inr_per_g"])
        exit_fill_a = float(trade["exit_fill_a_inr_per_g"])
        exit_fill_b = float(trade["exit_fill_b_inr_per_g"])
        trade_cost = float(trade["cost_inr"]) if pd.notna(trade["cost_inr"]) else 0.0

        sessions = [day for day in calendar.dates if entry_date <= day <= exit_date]
        if not sessions:
            sessions = [entry_date] if entry_date == exit_date else [entry_date, exit_date]

        # Calculate exact entry and exit fees if configs are available
        entry_fee, exit_fee = _calculate_trade_fees(
            side=side,
            entry_a=entry_fill_a,
            entry_b=entry_fill_b,
            exit_a=exit_fill_a,
            exit_b=exit_fill_b,
            qty_a=qty_a,
            qty_b=qty_b,
            symbol_a=pair[0],
            symbol_b=pair[2],
            total_trade_cost=trade_cost,
            costs=costs,
            contracts=contracts,
            multiplier=multiplier,
            signals_by_key=signals_by_key,
            entry_date=entry_date,
            exit_date=exit_date,
            pair=pair,
        )

        n_sessions = len(sessions)
        prev_close_a = entry_fill_a
        prev_close_b = entry_fill_b

        for idx, day in enumerate(sessions):
            active_dates.add(day)
            row = signals_by_key.get((day, *pair))

            # Determine day's start and end price for this trade
            if idx == 0 and n_sessions == 1:
                # Same day entry and exit
                start_a, end_a = entry_fill_a, exit_fill_a
                start_b, end_b = entry_fill_b, exit_fill_b
                day_fees = entry_fee + exit_fee
            elif idx == 0:
                # Entry session
                start_a = entry_fill_a
                end_a = float(row["price_a_inr_per_g"]) if row is not None else entry_fill_a
                start_b = entry_fill_b
                end_b = float(row["price_b_inr_per_g"]) if row is not None else entry_fill_b
                day_fees = entry_fee
                prev_close_a = end_a
                prev_close_b = end_b
            elif idx == n_sessions - 1:
                # Exit session
                start_a = prev_close_a
                end_a = exit_fill_a
                start_b = prev_close_b
                end_b = exit_fill_b
                day_fees = exit_fee
            else:
                # Holding session
                start_a = prev_close_a
                end_a = float(row["price_a_inr_per_g"]) if row is not None else prev_close_a
                start_b = prev_close_b
                end_b = float(row["price_b_inr_per_g"]) if row is not None else prev_close_b
                day_fees = 0.0
                prev_close_a = end_a
                prev_close_b = end_b

            dp_a = end_a - start_a
            dp_b = end_b - start_b

            gross_day = qty_a * dp_a + qty_b * dp_b
            ref_move = d_ref_fn(day)
            beta_day = res_g * ref_move
            alpha_day = qty_a * (dp_a - ref_move) + qty_b * (dp_b - ref_move)

            # Implied carry accrual (subset of alpha)
            carry_day = 0.0
            if row is not None and "carry_b_inr_per_g_per_day" in row:
                carry_rate = row["carry_b_inr_per_g_per_day"]
                if pd.notna(carry_rate) and math.isfinite(float(carry_rate)):
                    prev_day = sessions[idx - 1] if idx > 0 else day
                    cal_days = max(1, (day - prev_day).days)
                    carry_day = qty_b * float(carry_rate) * cal_days

            cost_day = -day_fees
            total_day = gross_day + cost_day

            daily_beta[day] += beta_day
            daily_alpha[day] += alpha_day
            daily_carry[day] += carry_day
            daily_cost[day] += cost_day
            daily_total[day] += total_day

    # Determine which dates to include
    if include_idle:
        all_dates = calendar.dates
    else:
        all_dates = sorted(active_dates)

    rows: list[dict[str, Any]] = []
    for day in all_dates:
        beta = daily_beta[day]
        alpha = daily_alpha[day]
        carry = daily_carry[day]
        cost = daily_cost[day]
        total = daily_total[day]
        residual = total - (beta + alpha + cost)
        if abs(residual) < 1e-12:
            residual = 0.0

        rows.append(
            {
                "trade_date": day,
                "beta_inr": beta,
                "alpha_inr": alpha,
                "carry_inr": carry,
                "cost_inr": cost,
                "residual_inr": residual,
                "total_pnl_inr": total,
            }
        )

    return pd.DataFrame(rows, columns=list(ATTRIBUTION_COLUMNS))


def verify_attribution_identity(attribution: pd.DataFrame, *, atol: float = 1e-9) -> bool:
    """Verify that total_pnl_inr == beta_inr + alpha_inr + cost_inr + residual_inr.

    Parameters
    ----------
    attribution : pd.DataFrame
        Attribution table to validate.
    atol : float, default 1e-9
        Absolute tolerance for floating point identity check.

    Returns
    -------
    bool
        True if the identity holds across all rows.
    """
    if attribution.empty:
        return True
    expected = (
        attribution["beta_inr"]
        + attribution["alpha_inr"]
        + attribution["cost_inr"]
        + attribution["residual_inr"]
    )
    diff = (attribution["total_pnl_inr"] - expected).abs()
    return bool((diff <= atol).all())


def _build_d_ref_evaluator(
    d_ref: float | pd.Series | Mapping[date, float] | Callable[[date], float] | str | None,
    signals: pd.DataFrame,
) -> Callable[[date], float]:
    """Convert d_ref input into a daily evaluator function returning INR/g move."""
    if d_ref is None:
        return lambda _: 0.0

    if isinstance(d_ref, (int, float)):
        val = float(d_ref)
        return lambda _: val

    if callable(d_ref):
        return lambda d: float(d_ref(d))

    if isinstance(d_ref, Mapping):
        return lambda d: float(d_ref.get(d, 0.0))

    if isinstance(d_ref, pd.Series):
        series_dict = {_as_date(idx): float(val) for idx, val in d_ref.items() if pd.notna(val)}
        return lambda d: float(series_dict.get(d, 0.0))

    if isinstance(d_ref, str):
        # Extract symbol's day-over-day price change from signals
        sym_rows = signals[signals["symbol_a"] == d_ref].sort_values("trade_date")
        if sym_rows.empty:
            sym_rows = signals[signals["symbol_b"] == d_ref].sort_values("trade_date")
            price_col = "price_b_inr_per_g"
        else:
            price_col = "price_a_inr_per_g"

        moves: dict[date, float] = {}
        prev_p: float | None = None
        for _, r in sym_rows.iterrows():
            d = _as_date(r["trade_date"])
            p = float(r[price_col])
            moves[d] = 0.0 if prev_p is None else p - prev_p
            prev_p = p
        return lambda d: float(moves.get(d, 0.0))

    raise TypeError(f"unsupported d_ref type: {type(d_ref).__name__}")


def _calculate_trade_fees(
    *,
    side: str,
    entry_a: float,
    entry_b: float,
    exit_a: float,
    exit_b: float,
    qty_a: float,
    qty_b: float,
    symbol_a: str,
    symbol_b: str,
    total_trade_cost: float,
    costs: CostsConfig | None,
    contracts: ContractsConfig | None,
    multiplier: float,
    signals_by_key: dict[tuple, pd.Series],
    entry_date: date,
    exit_date: date,
    pair: tuple,
) -> tuple[float, float]:
    """Calculate entry and exit fees so that entry_fee + exit_fee == total_trade_cost."""
    if costs is None or contracts is None or total_trade_cost == 0.0:
        half = total_trade_cost / 2.0
        return half, total_trade_cost - half

    entry_row = signals_by_key.get((entry_date, *pair))
    exit_row = signals_by_key.get((exit_date, *pair))
    thin_entry_a = bool(entry_row["thin_a"]) if entry_row is not None else False
    thin_entry_b = bool(entry_row["thin_b"]) if entry_row is not None else False
    thin_exit_a = bool(exit_row["thin_a"]) if exit_row is not None else False
    thin_exit_b = bool(exit_row["thin_b"]) if exit_row is not None else False

    entry_cost = _order_costs(
        side,
        closing=False,
        price_a=entry_a,
        price_b=entry_b,
        qty_a=qty_a,
        qty_b=qty_b,
        thin_a=thin_entry_a,
        thin_b=thin_entry_b,
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        costs=costs,
        contracts=contracts,
        multiplier=multiplier,
    )
    exit_cost = _order_costs(
        side,
        closing=True,
        price_a=exit_a,
        price_b=exit_b,
        qty_a=qty_a,
        qty_b=qty_b,
        thin_a=thin_exit_a,
        thin_b=thin_exit_b,
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        costs=costs,
        contracts=contracts,
        multiplier=multiplier,
    )

    if entry_cost is not None and exit_cost is not None and (entry_cost + exit_cost) > 0:
        return entry_cost, exit_cost

    half = total_trade_cost / 2.0
    return half, total_trade_cost - half


def _order_costs(
    side: str,
    *,
    closing: bool,
    price_a: float,
    price_b: float,
    qty_a: float,
    qty_b: float,
    thin_a: bool,
    thin_b: bool,
    symbol_a: str,
    symbol_b: str,
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float,
) -> float | None:
    sell_a = (side == "short_spread") if not closing else (side == "long_spread")
    a_side = "sell" if sell_a else "buy"
    b_side = "buy" if sell_a else "sell"
    legs = (
        (a_side, symbol_a, price_a, qty_a, thin_a),
        (b_side, symbol_b, price_b, qty_b, thin_b),
    )
    total = 0.0
    for fill_side, symbol, price, qty_g, thin in legs:
        order = order_cost_inr(
            symbol,
            fill_side,
            abs(qty_g) * price,
            thin=thin,
            costs=costs,
            contracts=contracts,
            multiplier=multiplier,
        )
        if order.total_inr is None:
            return None
        total += order.total_inr
    return total


def _as_date(val: Any) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return pd.Timestamp(val).date()


def _empty_attribution_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=list(ATTRIBUTION_COLUMNS))
    for col in ATTRIBUTION_COLUMNS:
        if col != "trade_date":
            df[col] = df[col].astype("float64")
    return df
