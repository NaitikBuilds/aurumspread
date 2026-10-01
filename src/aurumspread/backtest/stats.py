"""Performance statistics battery and metrics calculation for Task T11.

Computes Sharpe (cash and excess), hit rate, max drawdown (INR, %, duration),
turnover, cost drag, stationary block bootstrap p-value and confidence intervals,
and per-fold stability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from aurumspread.backtest.validation import SignificanceConfig

if TYPE_CHECKING:
    from aurumspread.backtest.engine import WalkForwardResult


@dataclass(frozen=True)
class PerformanceStats:
    """Complete summary of strategy backtest and validation metrics."""

    # Return & Risk
    annualized_return_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    excess_sharpe_ratio: float
    gross_sharpe_ratio: float
    sortino_ratio: float

    # Trade metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    hit_rate_pct: float
    profit_factor: float
    avg_trade_pnl_inr: float
    avg_hold_days: float

    # Drawdown
    max_drawdown_inr: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int

    # Turnover & Cost Drag
    annualized_turnover: float
    total_cost_inr: float
    cost_drag_pct: float
    cost_drag_bps: float

    # Stability & Significance
    stability_ratio: float | None
    min_fold_sharpe: float | None
    bootstrap_p_value: float
    bootstrap_ci_lower: float
    bootstrap_ci_upper: float
    deflated_sharpe_ratio: float | None
    n_trials: int | None


def _as_date(d: Any) -> date:
    if isinstance(d, date):
        return d
    return pd.Timestamp(d).date()


def compute_performance_stats(
    trades: pd.DataFrame,
    attribution: pd.DataFrame,
    capital_inr: float,
    *,
    annualization_factor: int = 250,
    risk_free_rate_pct: float = 6.5,
    subtract_risk_free: bool = False,
    folds: list[WalkForwardResult] | None = None,
    significance_cfg: SignificanceConfig | None = None,
    seed: int | None = None,
    n_trials: int | None = None,
) -> PerformanceStats:
    """Compute complete performance metrics from trades and attribution.

    Parameters
    ----------
    trades : pd.DataFrame
        Trade log containing closed spread trades.
    attribution : pd.DataFrame
        Daily session attribution containing total_pnl_inr and cost_inr.
    capital_inr : float
        Initial strategy portfolio capital in INR.
    annualization_factor : int, default 250
        Annual trading session count.
    risk_free_rate_pct : float, default 6.5
        Annualized risk-free rate percentage.
    subtract_risk_free : bool, default False
        If True, reports excess Sharpe as primary sharpe_ratio. Default is cash Sharpe.
    folds : list[WalkForwardResult], optional
        Walk-forward rolling folds for fold stability analysis.
    significance_cfg : SignificanceConfig, optional
        Bootstrap and statistical significance settings.
    seed : int, optional
        RNG seed for deterministic stationary bootstrap resampling.
    n_trials : int, optional
        Multiple-testing trial count for Deflated Sharpe. If None, deflated Sharpe is None.
    """
    if capital_inr <= 0:
        raise ValueError(f"capital_inr must be > 0, got {capital_inr}")

    sig_cfg = significance_cfg or SignificanceConfig()

    # 1. Daily return series from attribution
    if attribution is not None and not attribution.empty:
        attr_sorted = attribution.sort_values("trade_date").copy()
        daily_pnl = attr_sorted["total_pnl_inr"].astype(float).to_numpy()
        daily_cost = (
            attr_sorted["cost_inr"].astype(float).to_numpy()
            if "cost_inr" in attr_sorted.columns
            else np.zeros_like(daily_pnl)
        )
    else:
        daily_pnl = np.array([], dtype=float)
        daily_cost = np.array([], dtype=float)

    n_sessions = len(daily_pnl)
    r_daily = daily_pnl / capital_inr if n_sessions > 0 else np.array([], dtype=float)

    # Risk-free daily rate
    r_f_daily = (risk_free_rate_pct / 100.0) / annualization_factor

    # 2. Return & Volatility & Sharpe
    if n_sessions >= 2:
        mean_r = float(np.mean(r_daily))
        std_r = float(np.std(r_daily, ddof=1))
        ann_return_pct = mean_r * annualization_factor * 100.0
        ann_vol_pct = std_r * math.sqrt(annualization_factor) * 100.0

        if std_r > 0:
            sharpe_cash = math.sqrt(annualization_factor) * (mean_r / std_r)
            sharpe_excess = math.sqrt(annualization_factor) * ((mean_r - r_f_daily) / std_r)
        else:
            sharpe_cash = 0.0
            sharpe_excess = 0.0

        # Gross returns: gross_pnl = total_pnl - cost (since cost is negative in attribution)
        gross_daily = daily_pnl - daily_cost
        r_gross = gross_daily / capital_inr
        std_gross = float(np.std(r_gross, ddof=1))
        if std_gross > 0:
            gross_sharpe = math.sqrt(annualization_factor) * (float(np.mean(r_gross)) / std_gross)
        else:
            gross_sharpe = 0.0

        # Sortino
        diff_down = np.minimum(r_daily - (r_f_daily if subtract_risk_free else 0.0), 0.0)
        downside_std = math.sqrt(float(np.sum(diff_down**2) / (n_sessions - 1)))
        if downside_std > 0:
            sortino = math.sqrt(annualization_factor) * (
                (mean_r - (r_f_daily if subtract_risk_free else 0.0)) / downside_std
            )
        else:
            sortino = 0.0
    elif n_sessions == 1:
        mean_r = float(r_daily[0])
        ann_return_pct = mean_r * annualization_factor * 100.0
        ann_vol_pct = 0.0
        sharpe_cash = 0.0
        sharpe_excess = 0.0
        gross_sharpe = 0.0
        sortino = 0.0
    else:
        ann_return_pct = 0.0
        ann_vol_pct = 0.0
        sharpe_cash = 0.0
        sharpe_excess = 0.0
        gross_sharpe = 0.0
        sortino = 0.0

    primary_sharpe = sharpe_excess if subtract_risk_free else sharpe_cash

    # 3. Trade metrics
    if trades is not None and not trades.empty:
        total_trades = len(trades)
        net_pnls = trades["net_pnl_inr"].dropna().astype(float)
        winning_trades = int((net_pnls > 0).sum())
        losing_trades = int((net_pnls < 0).sum())
        hit_rate_pct = 100.0 * winning_trades / total_trades if total_trades > 0 else 0.0

        gross_wins = net_pnls[net_pnls > 0].sum()
        gross_losses = abs(net_pnls[net_pnls < 0].sum())
        if gross_losses > 0:
            profit_factor = float(gross_wins / gross_losses)
        elif gross_wins > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        avg_trade_pnl_inr = float(net_pnls.mean()) if total_trades > 0 else 0.0

        if "entry_fill_date" in trades.columns and "exit_fill_date" in trades.columns:
            durations = [
                (_as_date(exit_d) - _as_date(entry_d)).days
                for entry_d, exit_d in zip(
                    trades["entry_fill_date"], trades["exit_fill_date"], strict=True
                )
            ]
            avg_hold_days = float(np.mean(durations)) if durations else 0.0
        else:
            avg_hold_days = 0.0
    else:
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        hit_rate_pct = 0.0
        profit_factor = 0.0
        avg_trade_pnl_inr = 0.0
        avg_hold_days = 0.0

    # 4. Max Drawdown
    if n_sessions > 0:
        cum_pnl = np.cumsum(daily_pnl)
        equity = capital_inr + cum_pnl
        peak = np.maximum.accumulate(equity)
        dd_inr_series = peak - equity
        dd_pct_series = 100.0 * (peak - equity) / peak

        max_dd_inr = float(np.max(dd_inr_series))
        max_dd_pct = float(np.max(dd_pct_series))

        # Longest underwater duration (in sessions)
        underwater = equity < peak
        max_dd_duration = 0
        curr_dd_duration = 0
        for uw in underwater:
            if uw:
                curr_dd_duration += 1
                if curr_dd_duration > max_dd_duration:
                    max_dd_duration = curr_dd_duration
            else:
                curr_dd_duration = 0
    else:
        max_dd_inr = 0.0
        max_dd_pct = 0.0
        max_dd_duration = 0

    # 5. Turnover & Cost Drag
    if trades is not None and not trades.empty:
        total_cost_inr = float(abs(trades["cost_inr"].dropna().astype(float).sum()))
        gross_sum = float(trades["gross_pnl_inr"].dropna().astype(float).sum())

        notional_sum = 0.0
        for _, tr in trades.iterrows():
            qa = abs(float(tr.get("qty_g_a", 0.0)))
            qb = abs(float(tr.get("qty_g_b", 0.0)))
            ea = float(tr.get("entry_fill_a_inr_per_g", 0.0))
            eb = float(tr.get("entry_fill_b_inr_per_g", 0.0))
            xa = float(tr.get("exit_fill_a_inr_per_g", 0.0))
            xb = float(tr.get("exit_fill_b_inr_per_g", 0.0))
            notional_entry = qa * ea + qb * eb
            notional_exit = qa * xa + qb * xb
            notional_sum += notional_entry + notional_exit
    elif n_sessions > 0 and len(daily_cost) > 0:
        total_cost_inr = float(abs(np.sum(daily_cost)))
        gross_sum = float(np.sum(daily_pnl - daily_cost))
        notional_sum = 0.0
    else:
        total_cost_inr = 0.0
        gross_sum = 0.0
        notional_sum = 0.0

    if n_sessions > 0:
        annualized_turnover = (
            (annualization_factor / n_sessions) * (notional_sum / (2.0 * capital_inr))
            if notional_sum > 0
            else 0.0
        )
        cost_drag_bps = (
            (total_cost_inr / capital_inr) * (annualization_factor / n_sessions) * 10_000.0
        )
    else:
        annualized_turnover = 0.0
        cost_drag_bps = 0.0

    if gross_sum > 0:
        cost_drag_pct = 100.0 * (total_cost_inr / gross_sum)
    elif total_cost_inr > 0:
        cost_drag_pct = 100.0
    else:
        cost_drag_pct = 0.0

    # 6. Stationary Block Bootstrap for Sharpe p-value & 95% CI
    bs_p_val, ci_lower, ci_upper = _stationary_bootstrap_sharpe(
        r_daily,
        annualization_factor=annualization_factor,
        n_samples=sig_cfg.bootstrap_samples,
        mean_block_size=sig_cfg.block_size_days,
        ci_level=sig_cfg.ci_level,
        seed=seed,
    )

    # 7. Walk-forward Fold Stability
    stability_ratio, min_fold_sharpe = _compute_fold_stability(
        folds,
        annualization_factor=annualization_factor,
        capital_inr=capital_inr,
    )

    # 8. Deflated Sharpe Ratio
    if n_trials is not None and n_trials >= 1 and n_sessions >= 2 and std_r > 0:
        dsr = _compute_deflated_sharpe(
            primary_sharpe,
            r_daily,
            n_trials=n_trials,
            annualization_factor=annualization_factor,
        )
    else:
        dsr = None

    return PerformanceStats(
        annualized_return_pct=ann_return_pct,
        annualized_volatility_pct=ann_vol_pct,
        sharpe_ratio=primary_sharpe,
        excess_sharpe_ratio=sharpe_excess,
        gross_sharpe_ratio=gross_sharpe,
        sortino_ratio=sortino,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        hit_rate_pct=hit_rate_pct,
        profit_factor=profit_factor,
        avg_trade_pnl_inr=avg_trade_pnl_inr,
        avg_hold_days=avg_hold_days,
        max_drawdown_inr=max_dd_inr,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_days=max_dd_duration,
        annualized_turnover=annualized_turnover,
        total_cost_inr=total_cost_inr,
        cost_drag_pct=cost_drag_pct,
        cost_drag_bps=cost_drag_bps,
        stability_ratio=stability_ratio,
        min_fold_sharpe=min_fold_sharpe,
        bootstrap_p_value=bs_p_val,
        bootstrap_ci_lower=ci_lower,
        bootstrap_ci_upper=ci_upper,
        deflated_sharpe_ratio=dsr,
        n_trials=n_trials,
    )


def _stationary_bootstrap_sharpe(
    returns: np.ndarray,
    *,
    annualization_factor: int,
    n_samples: int,
    mean_block_size: int,
    ci_level: float,
    seed: int | None,
) -> tuple[float, float, float]:
    """Stationary bootstrap (Politis & Romano 1994) for Sharpe ratio."""
    n = len(returns)
    if n < 2 or np.all(returns == returns[0]):
        return 1.0, 0.0, 0.0

    p_geom = 1.0 / max(float(mean_block_size), 1.0)
    rng = np.random.default_rng(seed)

    boot_sharpes = np.empty(n_samples, dtype=float)

    for b in range(n_samples):
        # Generate block indices with geometric block length
        indices = np.empty(n, dtype=int)
        curr_idx = rng.integers(0, n)
        for i in range(n):
            if i > 0 and rng.random() < p_geom:
                curr_idx = rng.integers(0, n)
            else:
                curr_idx = (curr_idx + 1) % n if i > 0 else curr_idx
            indices[i] = curr_idx

        sample = returns[indices]
        s_std = float(np.std(sample, ddof=1))
        if s_std > 0:
            boot_sharpes[b] = math.sqrt(annualization_factor) * (float(np.mean(sample)) / s_std)
        else:
            boot_sharpes[b] = 0.0

    # Empirical p-value for H0: Sharpe <= 0
    p_val = float(np.mean(boot_sharpes <= 0.0))

    # Two-sided confidence interval
    alpha = 1.0 - ci_level
    ci_lower = float(np.quantile(boot_sharpes, alpha / 2.0))
    ci_upper = float(np.quantile(boot_sharpes, 1.0 - alpha / 2.0))

    return p_val, ci_lower, ci_upper


def _compute_fold_stability(
    folds: list[WalkForwardResult] | None,
    *,
    annualization_factor: int,
    capital_inr: float,
) -> tuple[float | None, float | None]:
    """Calculate stability ratio across rolling walk-forward folds."""
    if not folds:
        return None, None

    fold_sharpes: list[float] = []

    for f in folds:
        attr = getattr(f, "attribution", None)
        if attr is None or attr.empty or len(attr) < 2:
            continue
        pnl = attr["total_pnl_inr"].to_numpy(dtype=float)
        r = pnl / capital_inr
        s = float(np.std(r, ddof=1))
        if s > 0:
            fold_sharpes.append(math.sqrt(annualization_factor) * (float(np.mean(r)) / s))
        else:
            fold_sharpes.append(0.0)

    if not fold_sharpes:
        return None, None

    min_sharpe = float(min(fold_sharpes))
    mean_sharpe = float(np.mean(fold_sharpes))
    # Stability ratio: min fold sharpe / mean fold sharpe (or 1.0 if all identical)
    if mean_sharpe > 0:
        stability = min(max(min_sharpe / mean_sharpe, 0.0), 2.0)
    else:
        stability = 0.0

    return stability, min_sharpe


def _compute_deflated_sharpe(
    estimated_sharpe: float,
    returns: np.ndarray,
    *,
    n_trials: int,
    annualization_factor: int,
) -> float:
    """Compute Deflated Sharpe Ratio (Bailey & López de Prado 2014)."""
    n = len(returns)
    if n < 3:
        return 0.5

    skew = float(sp_stats.skew(returns))
    kurt = float(sp_stats.kurtosis(returns, fisher=False))  # Pearson kurtosis (normal=3)

    # Variance of annual Sharpe estimator
    sr = estimated_sharpe
    sr_daily = sr / math.sqrt(annualization_factor)
    v_daily = (1.0 - skew * sr_daily + ((kurt - 1.0) / 4.0) * (sr_daily**2)) / (n - 1)
    if v_daily <= 0:
        return 0.5

    v_ann = v_daily * annualization_factor
    std_sr = math.sqrt(v_ann)

    # Expected maximum Sharpe across N trials under H0
    euler_mascheroni = 0.57721566490153286
    z1 = sp_stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = sp_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    e_max = std_sr * ((1.0 - euler_mascheroni) * z1 + euler_mascheroni * z2)

    z_stat = (sr - e_max) / std_sr
    return float(sp_stats.norm.cdf(z_stat))
