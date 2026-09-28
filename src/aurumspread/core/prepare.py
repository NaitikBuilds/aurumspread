"""Glue: registry prices -> normalized + lifecycle-annotated frame ready for spreads.

This is the hand-off point to ``signals/`` and ``backtest/``: one call gives a
frame with ``pure_price_inr_per_g``, ``dte_days``, ``status`` and ``tradable``
on top of the validated registry columns.
"""

from __future__ import annotations

import pandas as pd

from aurumspread.config import AppConfig
from aurumspread.core.carry import build_pair_series
from aurumspread.core.lifecycle import annotate_lifecycle
from aurumspread.core.normalize import normalize_prices
from aurumspread.data.registry import ContractRegistry


def prepare_prices(registry: ContractRegistry, cfg: AppConfig) -> pd.DataFrame:
    """Normalize (PRD 6.1) and annotate lifecycle (PRD 6.6) for every registry row."""
    normalized = normalize_prices(registry.prices, cfg.contracts)
    return annotate_lifecycle(
        normalized, cfg.contracts, cfg.backtest.exit_buffer_days_before_expiry
    )


def pair_spreads(
    prepared: pd.DataFrame, symbol_a: str, symbol_b: str, cfg: AppConfig
) -> pd.DataFrame:
    """Carry-adjusted spread series for one ordered pair (PRD 6.2)."""
    return build_pair_series(prepared, symbol_a, symbol_b, cfg.backtest.alignment)


def all_pair_spreads(prepared: pd.DataFrame, cfg: AppConfig) -> pd.DataFrame:
    """Spread series for every ordered cross-family pair in the configured universe, stacked."""
    symbols = list(cfg.contracts.symbols)
    frames = [pair_spreads(prepared, a, b, cfg) for a in symbols for b in symbols if a != b]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
