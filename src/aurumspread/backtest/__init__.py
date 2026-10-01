"""Backtest: costs, liquidity, lot sizing, and the walk-forward engine."""

from aurumspread.backtest.attribution import (
    ATTRIBUTION_COLUMNS,
    compute_daily_attribution,
    flag_residual_outliers,
    verify_attribution_identity,
)
from aurumspread.backtest.costs import (
    OrderCost,
    RoundTripCost,
    estimate_round_trip_cost,
    order_cost_inr,
)
from aurumspread.backtest.engine import WalkForwardResult, walk_forward
from aurumspread.backtest.liquidity import (
    LiquidityDecision,
    cap_to_participation,
    filter_liquid,
    passes_liquidity,
)
from aurumspread.backtest.manifest import RunManifest, RunStamp, stamp_run
from aurumspread.backtest.sizing import SizedPair, size_pair
from aurumspread.backtest.stats import (
    PerformanceStats,
    compute_performance_stats,
)
from aurumspread.backtest.validation import (
    HurdlesConfig,
    SignificanceConfig,
    ValidationConfig,
    compute_validation_sha256,
    load_validation,
)

__all__ = [
    "ATTRIBUTION_COLUMNS",
    "HurdlesConfig",
    "LiquidityDecision",
    "OrderCost",
    "PerformanceStats",
    "RoundTripCost",
    "RunManifest",
    "RunStamp",
    "SignificanceConfig",
    "SizedPair",
    "ValidationConfig",
    "WalkForwardResult",
    "cap_to_participation",
    "compute_daily_attribution",
    "compute_performance_stats",
    "compute_validation_sha256",
    "estimate_round_trip_cost",
    "filter_liquid",
    "flag_residual_outliers",
    "load_validation",
    "order_cost_inr",
    "passes_liquidity",
    "size_pair",
    "stamp_run",
    "verify_attribution_identity",
    "walk_forward",
]
