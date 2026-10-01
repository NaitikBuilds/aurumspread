"""Backtest: costs, liquidity, lot sizing, and the walk-forward engine."""

from aurumspread.backtest.attribution import (
    ATTRIBUTION_COLUMNS,
    compute_daily_attribution,
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

__all__ = [
    "ATTRIBUTION_COLUMNS",
    "LiquidityDecision",
    "OrderCost",
    "RoundTripCost",
    "RunManifest",
    "RunStamp",
    "SizedPair",
    "WalkForwardResult",
    "cap_to_participation",
    "compute_daily_attribution",
    "estimate_round_trip_cost",
    "filter_liquid",
    "order_cost_inr",
    "passes_liquidity",
    "size_pair",
    "stamp_run",
    "verify_attribution_identity",
    "walk_forward",
]
