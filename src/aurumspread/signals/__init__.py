"""Spread signals: causal z-score and percentile on an aligned pair frame (T07)."""

from aurumspread.signals.spread import GROUP_COLS, build_pair_signals, build_spread_signals
from aurumspread.signals.zscore import (
    PERCENTILE_COL,
    SIGMA_COL,
    SIGNAL_COLUMNS,
    SPREAD_MEAN_COL,
    ZSCORE_COL,
    rolling_signal_arrays,
)

__all__ = [
    "GROUP_COLS",
    "PERCENTILE_COL",
    "SIGMA_COL",
    "SIGNAL_COLUMNS",
    "SPREAD_MEAN_COL",
    "ZSCORE_COL",
    "build_pair_signals",
    "build_spread_signals",
    "rolling_signal_arrays",
]
