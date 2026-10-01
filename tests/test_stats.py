"""Tests for performance statistics battery (Task T11.b, c, d)."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from aurumspread.backtest.stats import (
    compute_performance_stats,
)
from aurumspread.backtest.validation import SignificanceConfig


def test_hand_computed_performance_stats_fixture() -> None:
    """Exact hand-computed verification of all core performance metrics.

    Setup:
      capital_inr = 100,000.0
      annualization_factor = 250
      risk_free_rate_pct = 6.5 (daily rf = 0.065 / 250 = 0.00026)
      subtract_risk_free = False (primary = cash Sharpe)

    Attribution (4 sessions):
      Day 1: total_pnl = +1000.0, cost = -100.0 (gross = +1100.0) -> R = +0.010
      Day 2: total_pnl =  -500.0, cost =    0.0 (gross =  -500.0) -> R = -0.005
      Day 3: total_pnl = +1500.0, cost = -100.0 (gross = +1600.0) -> R = +0.015
      Day 4: total_pnl =  -200.0, cost =    0.0 (gross =  -200.0) -> R = -0.002

    Exact Calculations:
      Daily returns: [+0.010, -0.005, +0.015, -0.002]
      mean(R) = 0.018 / 4 = 0.0045
      annualized_return_pct = 0.0045 * 250 * 100 = 112.5%

      Sample Variance (ddof=1):
        d1 = 0.010 - 0.0045 =  0.0055 -> 0.00003025
        d2 = -0.005 - 0.0045 = -0.0095 -> 0.00009025
        d3 = 0.015 - 0.0045 =  0.0105 -> 0.00011025
        d4 = -0.002 - 0.0045 = -0.0065 -> 0.00004225
        Sum = 0.00027300
        Var = 0.00027300 / 3 = 0.000091
        std = sqrt(0.000091) ≈ 0.009539392014

      annualized_volatility_pct = std * sqrt(250) * 100 ≈ 15.08310313%
      sharpe_ratio (cash) = sqrt(250) * (0.0045 / std) ≈ 7.45869407

      excess daily mean = 0.0045 - (0.065 / 250) = 0.0045 - 0.00026 = 0.00424
      excess_sharpe_ratio = sqrt(250) * (0.00424 / std) ≈ 7.02773827

      Equity curve:
        Start: 100,000.0
        Day 1: 101,000.0 (peak 101,000.0, DD = 0)
        Day 2: 100,500.0 (peak 101,000.0, DD = 500.0, DD% = 500/101000 ≈ 0.4950495%)
        Day 3: 102,000.0 (peak 102,000.0, DD = 0)
        Day 4: 101,800.0 (peak 102,000.0, DD = 200.0, DD% = 200/102000 ≈ 0.1960784%)
      max_drawdown_inr = 500.0
      max_drawdown_pct = (500.0 / 101000.0) * 100 ≈ 0.4950495%
      max_drawdown_duration_days = 1 session

    Trades (2 trades):
      Trade 1: net = +500.0, gross = +600.0, cost = 100.0
      Trade 2: net = +1300.0, gross = +1400.0, cost = 100.0
      total_trades = 2, winning = 2, losing = 0, hit_rate = 100.0%
      profit_factor = inf
      avg_trade_pnl_inr = (500 + 1300) / 2 = 900.0
      cost_drag_pct = (200 / 2000) * 100 = 10.0%
    """
    days = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]
    attr = pd.DataFrame(
        [
            {"trade_date": days[0], "total_pnl_inr": 1000.0, "cost_inr": -100.0},
            {"trade_date": days[1], "total_pnl_inr": -500.0, "cost_inr": 0.0},
            {"trade_date": days[2], "total_pnl_inr": 1500.0, "cost_inr": -100.0},
            {"trade_date": days[3], "total_pnl_inr": -200.0, "cost_inr": 0.0},
        ]
    )

    trades = pd.DataFrame(
        [
            {
                "trade_id": 1,
                "net_pnl_inr": 500.0,
                "gross_pnl_inr": 600.0,
                "cost_inr": 100.0,
                "entry_fill_date": days[0],
                "exit_fill_date": days[1],
                "qty_g_a": 100.0,
                "qty_g_b": -100.0,
                "entry_fill_a_inr_per_g": 10.0,
                "entry_fill_b_inr_per_g": 10.0,
                "exit_fill_a_inr_per_g": 13.0,
                "exit_fill_b_inr_per_g": 10.0,
            },
            {
                "trade_id": 2,
                "net_pnl_inr": 1300.0,
                "gross_pnl_inr": 1400.0,
                "cost_inr": 100.0,
                "entry_fill_date": days[2],
                "exit_fill_date": days[3],
                "qty_g_a": 100.0,
                "qty_g_b": -100.0,
                "entry_fill_a_inr_per_g": 10.0,
                "entry_fill_b_inr_per_g": 10.0,
                "exit_fill_a_inr_per_g": 17.0,
                "exit_fill_b_inr_per_g": 10.0,
            },
        ]
    )

    stats = compute_performance_stats(
        trades=trades,
        attribution=attr,
        capital_inr=100_000.0,
        annualization_factor=250,
        risk_free_rate_pct=6.5,
        subtract_risk_free=False,
        seed=42,
    )

    # Returns & Volatility
    assert stats.annualized_return_pct == pytest.approx(112.5)
    expected_std = math.sqrt(0.000091)
    expected_vol_pct = expected_std * math.sqrt(250) * 100.0
    assert stats.annualized_volatility_pct == pytest.approx(expected_vol_pct)

    # Sharpe ratios
    expected_cash_sharpe = math.sqrt(250) * (0.0045 / expected_std)
    assert stats.sharpe_ratio == pytest.approx(expected_cash_sharpe)

    expected_excess_sharpe = math.sqrt(250) * ((0.0045 - 0.00026) / expected_std)
    assert stats.excess_sharpe_ratio == pytest.approx(expected_excess_sharpe)

    # Trade metrics
    assert stats.total_trades == 2
    assert stats.winning_trades == 2
    assert stats.losing_trades == 0
    assert stats.hit_rate_pct == 100.0
    assert stats.profit_factor == float("inf")
    assert stats.avg_trade_pnl_inr == 900.0
    assert stats.avg_hold_days == 1.0

    # Drawdown
    assert stats.max_drawdown_inr == pytest.approx(500.0)
    assert stats.max_drawdown_pct == pytest.approx((500.0 / 101000.0) * 100.0)
    assert stats.max_drawdown_duration_days == 1

    # Cost drag
    assert stats.total_cost_inr == pytest.approx(200.0)
    assert stats.cost_drag_pct == pytest.approx(10.0)  # 200 / 2000 * 100
    expected_bps = (200.0 / 100000.0) * (250 / 4) * 10000.0
    assert stats.cost_drag_bps == pytest.approx(expected_bps)


def test_stationary_bootstrap_determinism_and_seeding() -> None:
    """Stationary bootstrap is bitwise deterministic given the same seed."""
    np.random.seed(0)
    r = np.random.normal(0.001, 0.01, size=50)
    dates = [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(50)]
    attr = pd.DataFrame({"trade_date": dates, "total_pnl_inr": r * 100_000.0})
    trades = pd.DataFrame()

    sig_cfg = SignificanceConfig(bootstrap_samples=500, block_size_days=5)

    stats1 = compute_performance_stats(
        trades=trades,
        attribution=attr,
        capital_inr=100_000.0,
        significance_cfg=sig_cfg,
        seed=12345,
    )

    stats2 = compute_performance_stats(
        trades=trades,
        attribution=attr,
        capital_inr=100_000.0,
        significance_cfg=sig_cfg,
        seed=12345,
    )

    # Identical seed yields exact same bootstrap values
    assert stats1.bootstrap_p_value == stats2.bootstrap_p_value
    assert stats1.bootstrap_ci_lower == stats2.bootstrap_ci_lower
    assert stats1.bootstrap_ci_upper == stats2.bootstrap_ci_upper

    # Different seed yields slightly different bootstrap sampling
    stats3 = compute_performance_stats(
        trades=trades,
        attribution=attr,
        capital_inr=100_000.0,
        significance_cfg=sig_cfg,
        seed=99999,
    )
    assert stats3.bootstrap_ci_lower != stats1.bootstrap_ci_lower


def test_deflated_sharpe_ratio() -> None:
    """Deflated Sharpe is computed when n_trials is provided, None otherwise."""
    dates = [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(100)]
    r = np.random.default_rng(42).normal(0.001, 0.01, size=100)
    attr = pd.DataFrame({"trade_date": dates, "total_pnl_inr": r * 100_000.0})

    # n_trials is None -> deflated Sharpe unavailable
    s_none = compute_performance_stats(pd.DataFrame(), attr, capital_inr=100_000.0, n_trials=None)
    assert s_none.deflated_sharpe_ratio is None
    assert s_none.n_trials is None

    # n_trials is provided -> deflated Sharpe computed in [0, 1]
    s_trials = compute_performance_stats(pd.DataFrame(), attr, capital_inr=100_000.0, n_trials=10)
    assert s_trials.deflated_sharpe_ratio is not None
    assert 0.0 <= s_trials.deflated_sharpe_ratio <= 1.0
    assert s_trials.n_trials == 10


def test_per_fold_stability() -> None:
    """Per-fold stability ratio is computed across walk-forward folds."""
    from dataclasses import dataclass

    @dataclass
    class MockFoldResult:
        attribution: pd.DataFrame

    days = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    fold1 = MockFoldResult(
        pd.DataFrame(
            [
                {"trade_date": days[0], "total_pnl_inr": 1000.0},
                {"trade_date": days[1], "total_pnl_inr": 1200.0},
                {"trade_date": days[2], "total_pnl_inr": 1100.0},
            ]
        )
    )
    fold2 = MockFoldResult(
        pd.DataFrame(
            [
                {"trade_date": days[0], "total_pnl_inr": 500.0},
                {"trade_date": days[1], "total_pnl_inr": 600.0},
                {"trade_date": days[2], "total_pnl_inr": 550.0},
            ]
        )
    )

    stats = compute_performance_stats(
        pd.DataFrame(),
        fold1.attribution,
        capital_inr=100_000.0,
        folds=[fold1, fold2],  # type: ignore[list-item]
    )

    assert stats.stability_ratio is not None
    assert stats.min_fold_sharpe is not None
    assert stats.stability_ratio > 0.0
