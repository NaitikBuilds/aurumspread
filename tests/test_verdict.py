"""Table-driven tests covering evaluate_verdict (Task T11.e).

Covers all pass / fail / inconclusive branches and each individual fatal rule.
"""

import pytest

from aurumspread.backtest.stats import PerformanceStats
from aurumspread.backtest.validation import (
    HurdlesConfig,
    SignificanceConfig,
    ValidationConfig,
)
from aurumspread.backtest.verdict import evaluate_verdict


def _make_stats(**kwargs) -> PerformanceStats:
    """Helper to instantiate PerformanceStats with healthy passing defaults."""
    defaults = {
        "annualized_return_pct": 25.0,
        "annualized_volatility_pct": 15.0,
        "sharpe_ratio": 1.5,
        "excess_sharpe_ratio": 1.1,
        "gross_sharpe_ratio": 1.8,
        "sortino_ratio": 2.0,
        "total_trades": 40,
        "winning_trades": 24,
        "losing_trades": 16,
        "hit_rate_pct": 60.0,
        "profit_factor": 1.8,
        "avg_trade_pnl_inr": 500.0,
        "avg_hold_days": 3.0,
        "max_drawdown_inr": 5000.0,
        "max_drawdown_pct": 10.0,
        "max_drawdown_duration_days": 10,
        "annualized_turnover": 4.5,
        "total_cost_inr": 1500.0,
        "cost_drag_pct": 20.0,
        "cost_drag_bps": 150.0,
        "stability_ratio": 0.80,
        "min_fold_sharpe": 1.1,
        "bootstrap_p_value": 0.02,
        "bootstrap_ci_lower": 0.5,
        "bootstrap_ci_upper": 2.5,
        "deflated_sharpe_ratio": 0.85,
        "n_trials": 5,
    }
    defaults.update(kwargs)
    return PerformanceStats(**defaults)


def test_verdict_clean_pass() -> None:
    """When all hurdles are met with sufficient sample size, verdict is 'pass'."""
    cfg = ValidationConfig()
    stats = _make_stats()
    verdict = evaluate_verdict(stats, cfg)

    assert verdict.status == "pass"
    assert verdict.thresholds_provisional is True
    assert len(verdict.validation_sha256) == 64
    assert all(verdict.hurdle_results.values())
    assert "All validation hurdles satisfied" in verdict.reasons[0]


@pytest.mark.parametrize(
    ("override", "expected_fatal_substring"),
    [
        ({"sharpe_ratio": -0.1}, "fatal floor"),
        ({"max_drawdown_pct": 28.0}, "fatal ceiling"),
        ({"cost_drag_pct": 105.0}, "fatal ceiling"),
        ({"bootstrap_p_value": 0.25}, "fatal cutoff"),
    ],
)
def test_verdict_fatal_breaches_trigger_immediate_fail(
    override: dict, expected_fatal_substring: str
) -> None:
    """Any fatal breach immediately fails, even if other stats pass."""
    cfg = ValidationConfig()
    stats = _make_stats(**override)
    verdict = evaluate_verdict(stats, cfg)

    assert verdict.status == "fail"
    assert any(expected_fatal_substring in r for r in verdict.reasons)


@pytest.mark.parametrize(
    ("override", "expected_inconclusive_substring"),
    [
        ({"total_trades": 15}, "Insufficient sample size"),
        ({"bootstrap_p_value": 0.08}, "Marginal significance"),
        ({"total_trades": 10, "bootstrap_p_value": 0.12}, "Insufficient sample size"),
    ],
)
def test_verdict_insufficient_evidence_triggers_inconclusive(
    override: dict, expected_inconclusive_substring: str
) -> None:
    """Insufficient sample size or marginal significance triggers 'inconclusive'."""
    cfg = ValidationConfig()
    stats = _make_stats(**override)
    verdict = evaluate_verdict(stats, cfg)

    assert verdict.status == "inconclusive"
    assert any(expected_inconclusive_substring in r for r in verdict.reasons)


@pytest.mark.parametrize(
    ("override", "expected_failed_hurdle"),
    [
        ({"sharpe_ratio": 0.8}, "min_sharpe"),
        ({"max_drawdown_pct": 18.0}, "max_drawdown_pct"),
        ({"hit_rate_pct": 42.0}, "min_hit_rate_pct"),
        ({"cost_drag_pct": 40.0}, "max_cost_drag_pct"),
        ({"stability_ratio": 0.50}, "min_stability_ratio"),
    ],
)
def test_verdict_missed_standard_hurdles_trigger_fail(
    override: dict, expected_failed_hurdle: str
) -> None:
    """With sufficient evidence (trades >= 30), failing a standard hurdle causes 'fail'."""
    cfg = ValidationConfig()
    stats = _make_stats(**override)
    verdict = evaluate_verdict(stats, cfg)

    assert verdict.status == "fail"
    assert verdict.hurdle_results[expected_failed_hurdle] is False
    assert any(expected_failed_hurdle in r for r in verdict.reasons)


def test_verdict_thresholds_provisional_flag_forwarded() -> None:
    """StrategyVerdict forwards the thresholds_provisional flag from config."""
    cfg_provisional = ValidationConfig(thresholds_provisional=True)
    stats = _make_stats()
    v1 = evaluate_verdict(stats, cfg_provisional)
    assert v1.thresholds_provisional is True

    cfg_locked = ValidationConfig(
        thresholds_provisional=False,
        hurdles=HurdlesConfig(),
        significance=SignificanceConfig(),
    )
    v2 = evaluate_verdict(stats, cfg_locked)
    assert v2.thresholds_provisional is False
