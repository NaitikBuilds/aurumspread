"""Attach causal z-score and percentile to an aligned spread frame (T07).

The carry-adjusted spread is built by :func:`aurumspread.core.prepare.pair_spreads`.
This module does not re-pair expiries or recompute carry. Each signal series is
one ``(symbol, expiry)`` pair. Rolls are a new series, not a continuous near month.
"""

from __future__ import annotations

import pandas as pd

from aurumspread.config import AppConfig
from aurumspread.core.carry import SPREAD_COL
from aurumspread.core.prepare import pair_spreads
from aurumspread.signals.zscore import SIGNAL_COLUMNS, rolling_signal_arrays

GROUP_COLS = ("symbol_a", "expiry_a", "symbol_b", "expiry_b")
_REQUIRED = (*GROUP_COLS, "trade_date", SPREAD_COL)


def build_spread_signals(pairs: pd.DataFrame, window: int) -> pd.DataFrame:
    """Copy ``pairs`` and add :data:`~aurumspread.signals.zscore.SIGNAL_COLUMNS`.

    ``window`` is passed in (``BacktestConfig.zscore_window_days``); this
    function does not read YAML. Rows are sorted by the contract-pair key and
    then ``trade_date``. Duplicate dates inside one pair raise ``ValueError``.
    The input frame is not mutated.
    """
    missing = [column for column in _REQUIRED if column not in pairs.columns]
    if missing:
        raise KeyError(f"pairs frame lacks columns {missing}")
    out = pairs.copy()
    for column in SIGNAL_COLUMNS:
        if column in out.columns:
            out = out.drop(columns=[column])
    if out.empty:
        for column in SIGNAL_COLUMNS:
            out[column] = pd.Series(dtype="float64")
        return out

    blocks: list[pd.DataFrame] = []
    grouped = out.groupby(list(GROUP_COLS), sort=True, dropna=False)
    for key, group in grouped:
        ordered = group.sort_values("trade_date", kind="stable")
        if ordered["trade_date"].duplicated().any():
            raise ValueError(f"duplicate trade_date in pair {key}")
        stats = rolling_signal_arrays(ordered[SPREAD_COL].to_numpy(), window)
        block = ordered.copy()
        for column, values in stats.items():
            block[column] = values
        blocks.append(block)
    return pd.concat(blocks, ignore_index=True)


def build_pair_signals(
    prepared: pd.DataFrame,
    symbol_a: str,
    symbol_b: str,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Carry-adjusted pair series for one ordered pair, plus causal signals.

    Spread construction is :func:`aurumspread.core.prepare.pair_spreads`.
    The z-score window is ``cfg.backtest.zscore_window_days``.
    """
    pairs = pair_spreads(prepared, symbol_a, symbol_b, cfg)
    return build_spread_signals(pairs, cfg.backtest.zscore_window_days)
