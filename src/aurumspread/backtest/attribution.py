"""Daily P&L attribution (PRD Section 8, task T10).

Decomposes daily strategy performance into:
- Beta: net unhedged gold exposure times reference gold move ((sum g_i) * dRef).
- Alpha: relative-value spread move sum g_i * (dP_i - dRef).
- Carry: roll-down / implied carry accrual (reported separately as a subset of alpha).
- Cost: signed drag of transaction fees and slippage (<= 0.0).
- Residual: tracking difference between independent mark-to-market total P&L and
  attribution model components (total_pnl_inr - (beta_inr + alpha_inr + cost_inr)).

Mathematical identity:
    total_pnl_inr = beta_inr + alpha_inr + cost_inr + residual_inr

What residual_inr DOES test:
- Independent MTM vs model decomposition tracking: verifies whether actual
  settlement marks (from Person 1's normalized price frame) and execution fills
  match the signal price series.
- Mark timing and missing marks: mid-hold missing marks produce non-zero per-day
  residuals that reverse once marks resume (cumulative residual sums to zero).
- Execution vs mark mismatches: fill differences on entry and exit sessions.
- Accounting consistency: confirms fees and leg exposures balance algebraically.

What residual_inr DOES NOT test:
- Economic profitability or trading alpha quality (tested by net P&L and Sharpe).
- Statistical significance or overfitting (tested by the validation battery).
- When mark_prices is not provided separately from signals, holding-day MTM
  falls back to the signal series, so holding-day residual is zero by construction
  and does not test for external mark divergence.
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
    daily_mtm: Mapping[date, float] | pd.Series | None = None,
    mark_prices: pd.DataFrame | None = None,
    include_idle: bool = False,
) -> pd.DataFrame:
    """Compute daily attribution from closed trades and signal price series.

    total_pnl_inr is computed independently from the trade log / mark-to-market
    (entry/exit execution fills and daily settlement marks minus fees paid, or
    daily_mtm if provided), not from the sum of the attribution columns.
    residual_inr measures any tracking difference between mark-to-market P&L
    and (beta_inr + alpha_inr + cost_inr).

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
        (where beta is zero by construction). A symbol name (e.g. "GOLDM")
        extracts daily reference returns from the signal frame.
    calendar : TradingCalendar, optional
        Trading calendar for determining sessions between entry and exit.
    costs : CostsConfig, optional
        Cost configuration for exact daily order fee splits.
    contracts : ContractsConfig, optional
        Contract specifications.
    multiplier : float, default 1.0
        Stress multiplier on fees and slippage.
    daily_mtm : Mapping or Series, optional
        Independent daily mark-to-market P&L stream if supplied externally.
    mark_prices : pd.DataFrame, optional
        Independent contract closing marks if distinct from signals.
    include_idle : bool, default False
        If True, includes calendar dates with zero P&L when no book was open.

    Returns
    -------
    pd.DataFrame
        Daily attribution table keyed by session trade_date.
    """
    if hasattr(trades, "attribution") and isinstance(trades.attribution, pd.DataFrame):
        if d_ref is None and costs is None and not include_idle and daily_mtm is None:
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

    marks_lookup: dict[tuple[date, str, date], float] = {}
    pair_marks_lookup: dict[tuple, pd.Series] = {}
    has_custom_marks = False
    if mark_prices is not None and not mark_prices.empty:
        has_custom_marks = True
        mp = mark_prices.copy()
        mp["trade_date"] = mp["trade_date"].map(_as_date)
        if "symbol" in mp.columns and "expiry_date" in mp.columns:
            # Person 1's contract-level normalized price frame
            mp["expiry_date"] = mp["expiry_date"].map(_as_date)
            p_col = (
                "pure_price_inr_per_g"
                if "pure_price_inr_per_g" in mp.columns
                else ("close_inr" if "close_inr" in mp.columns else None)
            )
            if p_col:
                for _, r in mp.iterrows():
                    val = r[p_col]
                    if pd.notna(val):
                        key = (
                            _as_date(r["trade_date"]),
                            str(r["symbol"]),
                            _as_date(r["expiry_date"]),
                        )
                        marks_lookup[key] = float(val)
        elif "symbol_a" in mp.columns and "expiry_a" in mp.columns:
            # Pair-level marks frame
            for col in ("expiry_a", "expiry_b"):
                if col in mp.columns:
                    mp[col] = mp[col].map(_as_date)
            for _, r in mp.iterrows():
                key_pair = (
                    _as_date(r["trade_date"]),
                    r["symbol_a"],
                    _as_date(r["expiry_a"]),
                    r["symbol_b"],
                    _as_date(r["expiry_b"]),
                )
                pair_marks_lookup[key_pair] = r

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
        mtm_prev_close_a = entry_fill_a
        mtm_prev_close_b = entry_fill_b
        sig_prev_close_a: float | None = None
        sig_prev_close_b: float | None = None

        for idx, day in enumerate(sessions):
            active_dates.add(day)
            sig_row = signals_by_key.get((day, *pair))
            if has_custom_marks:
                if marks_lookup:
                    mark_a = marks_lookup.get((day, pair[0], pair[1]))
                    mark_b = marks_lookup.get((day, pair[2], pair[3]))
                else:
                    p_row = pair_marks_lookup.get((day, *pair))
                    mark_a = (
                        float(p_row["price_a_inr_per_g"])
                        if (p_row is not None and pd.notna(p_row.get("price_a_inr_per_g")))
                        else None
                    )
                    mark_b = (
                        float(p_row["price_b_inr_per_g"])
                        if (p_row is not None and pd.notna(p_row.get("price_b_inr_per_g")))
                        else None
                    )
            else:
                # Fallback to signal prices: holding-day residual is 0 by construction
                mark_a = (
                    float(sig_row["price_a_inr_per_g"])
                    if (sig_row is not None and pd.notna(sig_row.get("price_a_inr_per_g")))
                    else None
                )
                mark_b = (
                    float(sig_row["price_b_inr_per_g"])
                    if (sig_row is not None and pd.notna(sig_row.get("price_b_inr_per_g")))
                    else None
                )

            # 1. Independent Mark-to-Market P&L from execution fills and settlement marks
            if idx == 0 and n_sessions == 1:
                mtm_start_a, mtm_end_a = entry_fill_a, exit_fill_a
                mtm_start_b, mtm_end_b = entry_fill_b, exit_fill_b
                day_fees = entry_fee + exit_fee
            elif idx == 0:
                mtm_start_a = entry_fill_a
                mtm_end_a = mark_a if mark_a is not None else entry_fill_a
                mtm_start_b = entry_fill_b
                mtm_end_b = mark_b if mark_b is not None else entry_fill_b
                day_fees = entry_fee
                mtm_prev_close_a = mtm_end_a
                mtm_prev_close_b = mtm_end_b
            elif idx == n_sessions - 1:
                mtm_start_a = mtm_prev_close_a
                mtm_end_a = exit_fill_a
                mtm_start_b = mtm_prev_close_b
                mtm_end_b = exit_fill_b
                day_fees = exit_fee
            else:
                mtm_start_a = mtm_prev_close_a
                mtm_end_a = mark_a if mark_a is not None else mtm_prev_close_a
                mtm_start_b = mtm_prev_close_b
                mtm_end_b = mark_b if mark_b is not None else mtm_prev_close_b
                day_fees = 0.0
                mtm_prev_close_a = mtm_end_a
                mtm_prev_close_b = mtm_end_b

            dp_mtm_a = mtm_end_a - mtm_start_a
            dp_mtm_b = mtm_end_b - mtm_start_b
            mtm_gross_day = qty_a * dp_mtm_a + qty_b * dp_mtm_b
            total_day = mtm_gross_day - day_fees

            # 2. Attribution model evaluation from signal price series
            curr_sig_a = (
                float(sig_row["price_a_inr_per_g"])
                if (sig_row is not None and pd.notna(sig_row["price_a_inr_per_g"]))
                else None
            )
            curr_sig_b = (
                float(sig_row["price_b_inr_per_g"])
                if (sig_row is not None and pd.notna(sig_row["price_b_inr_per_g"]))
                else None
            )

            if idx == 0 and n_sessions == 1:
                # Same-session trade: price change between entry fill and exit fill
                dp_sig_a = exit_fill_a - entry_fill_a
                dp_sig_b = exit_fill_b - entry_fill_b
                sig_prev_close_a = curr_sig_a
                sig_prev_close_b = curr_sig_b
            elif idx == 0:
                # Model signal change on entry session of multi-day hold
                dp_sig_a = 0.0
                dp_sig_b = 0.0
                sig_prev_close_a = curr_sig_a
                sig_prev_close_b = curr_sig_b
            else:
                dp_sig_a = (
                    (curr_sig_a - sig_prev_close_a)
                    if (curr_sig_a is not None and sig_prev_close_a is not None)
                    else 0.0
                )
                dp_sig_b = (
                    (curr_sig_b - sig_prev_close_b)
                    if (curr_sig_b is not None and sig_prev_close_b is not None)
                    else 0.0
                )
                if curr_sig_a is not None:
                    sig_prev_close_a = curr_sig_a
                if curr_sig_b is not None:
                    sig_prev_close_b = curr_sig_b

            ref_move = d_ref_fn(day)
            beta_day = res_g * ref_move
            alpha_day = qty_a * (dp_sig_a - ref_move) + qty_b * (dp_sig_b - ref_move)

            # Implied carry accrual (subset of alpha; strictly 0.0 on entry session)
            carry_day = 0.0
            if idx > 0 and sig_row is not None and "carry_b_inr_per_g_per_day" in sig_row:
                carry_rate = sig_row["carry_b_inr_per_g_per_day"]
                if pd.notna(carry_rate) and math.isfinite(float(carry_rate)):
                    prev_day = sessions[idx - 1]
                    cal_days = max(1, (day - prev_day).days)
                    carry_day = qty_b * float(carry_rate) * cal_days

            cost_day = -day_fees

            daily_beta[day] += beta_day
            daily_alpha[day] += alpha_day
            daily_carry[day] += carry_day
            daily_cost[day] += cost_day
            daily_total[day] += total_day

    # Apply external daily MTM override if supplied
    if daily_mtm is not None:
        if isinstance(daily_mtm, pd.Series):
            for d, val in daily_mtm.items():
                date_key = _as_date(d)
                if pd.notna(val):
                    daily_total[date_key] = float(val)
                    active_dates.add(date_key)
        elif isinstance(daily_mtm, Mapping):
            for d, val in daily_mtm.items():
                date_key = _as_date(d)
                if pd.notna(val):
                    daily_total[date_key] = float(val)
                    active_dates.add(date_key)

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
        # residual is the difference between independent total and attribution components
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


def flag_residual_outliers(
    attribution: pd.DataFrame,
    tolerance: float,
) -> pd.DataFrame:
    """Flag sessions where |residual_inr| exceeds the configured tolerance.

    Parameters
    ----------
    attribution : pd.DataFrame
        Daily attribution table containing 'residual_inr'.
    tolerance : float
        Maximum allowable absolute residual in INR before flagging. Callers
        typically pass `backtest.residual_tolerance_inr` from configuration.
        Must be a non-negative float.

    Returns
    -------
    pd.DataFrame
        Subset of attribution rows where |residual_inr| > tolerance.
    """
    if attribution.empty:
        return attribution.copy()

    tol = float(tolerance)
    if tol < 0.0:
        raise ValueError(f"tolerance must be non-negative, got {tolerance}")

    mask = attribution["residual_inr"].abs() > tol
    return attribution.loc[mask].copy().reset_index(drop=True)


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
