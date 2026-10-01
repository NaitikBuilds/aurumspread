"""Strategy verdict evaluation logic for Task T11 (verdict per backtest protocol).

Evaluates PerformanceStats against ValidationConfig hurdles, determining whether
the strategy achieves a 'pass', 'fail', or 'inconclusive' verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aurumspread.backtest.stats import PerformanceStats
from aurumspread.backtest.validation import ValidationConfig

VerdictStatus = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class StrategyVerdict:
    """Automated evaluation verdict of strategy backtest and statistical validation."""

    status: VerdictStatus
    thresholds_provisional: bool
    validation_sha256: str
    hurdle_results: dict[str, bool]
    reasons: list[str]
    stats: PerformanceStats


def evaluate_verdict(
    stats: PerformanceStats,
    validation_cfg: ValidationConfig,
) -> StrategyVerdict:
    """Evaluate config-driven pass / fail / inconclusive verdict against validation hurdles.

    Verdict Decision Rules:
    1. 'fail':
       - Any fatal threshold breached immediately triggers 'fail', regardless of trade count.
       - Or sufficient evidence (trades >= min_trades and p < fatal_p_value_threshold),
         but one or more standard pass hurdles are failed.
    2. 'inconclusive':
       - Avoids all fatal traps, but evidence is insufficient:
         - total_trades < min_trades (insufficient sample size)
         - or bootstrap p-value in marginal band [p_value_threshold, fatal_p_value_threshold).
    3. 'pass':
       - No fatal conditions.
       - Sufficient evidence: total_trades >= min_trades and bootstrap p-value < p_value_threshold.
       - All standard pass hurdles satisfied simultaneously.
    """
    hurdles = validation_cfg.hurdles
    sig = validation_cfg.significance

    hurdle_results: dict[str, bool] = {
        "min_sharpe": stats.sharpe_ratio >= hurdles.min_sharpe,
        "max_drawdown_pct": stats.max_drawdown_pct <= hurdles.max_drawdown_pct,
        "min_hit_rate_pct": stats.hit_rate_pct >= hurdles.min_hit_rate_pct,
        "max_cost_drag_pct": stats.cost_drag_pct <= hurdles.max_cost_drag_pct,
        "min_trades": stats.total_trades >= validation_cfg.min_trades,
        "min_stability_ratio": (
            stats.stability_ratio >= hurdles.min_stability_ratio
            if stats.stability_ratio is not None
            else True
        ),
        "p_value_threshold": stats.bootstrap_p_value < sig.p_value_threshold,
    }

    fatal_reasons: list[str] = []
    if stats.sharpe_ratio < hurdles.fatal_sharpe:
        msg = f"Sharpe ({stats.sharpe_ratio:.2f}) breached fatal floor ({hurdles.fatal_sharpe:.2f})"
        fatal_reasons.append(msg)
    if stats.max_drawdown_pct > hurdles.fatal_drawdown_pct:
        msg = (
            f"Max drawdown ({stats.max_drawdown_pct:.1f}%) breached "
            f"fatal ceiling ({hurdles.fatal_drawdown_pct:.1f}%)"
        )
        fatal_reasons.append(msg)
    if stats.cost_drag_pct > hurdles.fatal_cost_drag_pct:
        msg = (
            f"Cost drag ({stats.cost_drag_pct:.1f}%) breached "
            f"fatal ceiling ({hurdles.fatal_cost_drag_pct:.1f}%)"
        )
        fatal_reasons.append(msg)
    if stats.bootstrap_p_value >= sig.fatal_p_value_threshold:
        msg = (
            f"Bootstrap p-value ({stats.bootstrap_p_value:.3f}) breached "
            f"fatal cutoff ({sig.fatal_p_value_threshold:.3f})"
        )
        fatal_reasons.append(msg)

    # 1. Fatal breach -> immediate fail
    if fatal_reasons:
        return StrategyVerdict(
            status="fail",
            thresholds_provisional=validation_cfg.thresholds_provisional,
            validation_sha256=validation_cfg.validation_sha256(),
            hurdle_results=hurdle_results,
            reasons=fatal_reasons,
            stats=stats,
        )

    # 2. Check for insufficient evidence -> inconclusive
    insufficient_reasons: list[str] = []
    if not hurdle_results["min_trades"]:
        msg = (
            f"Insufficient sample size: {stats.total_trades} trades < "
            f"min_trades ({validation_cfg.min_trades})"
        )
        insufficient_reasons.append(msg)
    if stats.bootstrap_p_value >= sig.p_value_threshold:
        msg = (
            f"Marginal significance: bootstrap p-value {stats.bootstrap_p_value:.3f} >= "
            f"alpha ({sig.p_value_threshold:.3f})"
        )
        insufficient_reasons.append(msg)

    if insufficient_reasons:
        return StrategyVerdict(
            status="inconclusive",
            thresholds_provisional=validation_cfg.thresholds_provisional,
            validation_sha256=validation_cfg.validation_sha256(),
            hurdle_results=hurdle_results,
            reasons=insufficient_reasons,
            stats=stats,
        )

    # 3. Check standard hurdles with sufficient evidence
    failed_hurdles: list[str] = []
    for hurdle_name, passed in hurdle_results.items():
        if not passed:
            failed_hurdles.append(f"Hurdle '{hurdle_name}' failed")

    if failed_hurdles:
        return StrategyVerdict(
            status="fail",
            thresholds_provisional=validation_cfg.thresholds_provisional,
            validation_sha256=validation_cfg.validation_sha256(),
            hurdle_results=hurdle_results,
            reasons=failed_hurdles,
            stats=stats,
        )

    # 4. All hurdles passed with sufficient evidence
    return StrategyVerdict(
        status="pass",
        thresholds_provisional=validation_cfg.thresholds_provisional,
        validation_sha256=validation_cfg.validation_sha256(),
        hurdle_results=hurdle_results,
        reasons=["All validation hurdles satisfied with statistical significance"],
        stats=stats,
    )
