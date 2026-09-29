"""Run manifest for a walk-forward result (BACKTEST_PROTOCOL, FR-9).

The manifest is a pure function of the inputs. It has no wall-clock time, so
two runs on the same frames and config produce the same hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import pandas as pd

from aurumspread.config import BacktestConfig, ContractsConfig, CostsConfig


@dataclass(frozen=True)
class RunManifest:
    """Identity of one engine run. ``git_commit`` is supplied by the caller."""

    git_commit: str | None
    config_sha256: str
    inputs_sha256: str
    start_date: date | None
    end_date: date | None
    seed: int
    n_trades: int
    n_skips: int
    split_frozen: bool


def config_sha256(
    backtest: BacktestConfig,
    costs: CostsConfig,
    contracts: ContractsConfig,
    *,
    target_g: float,
    multiplier: float,
    allow_test: bool,
) -> str:
    """SHA-256 of the config objects and the run arguments that affect trades."""
    payload = {
        "allow_test": allow_test,
        "backtest": backtest.model_dump(mode="json"),
        "contracts": contracts.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "multiplier": multiplier,
        "target_g": target_g,
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def frame_sha256(frame: pd.DataFrame) -> str:
    """SHA-256 of a frame in a stable column and row order."""
    ordered = frame.copy()
    sort_cols = [column for column in ordered.columns if column in _SORT_HINTS]
    if sort_cols:
        ordered = ordered.sort_values(sort_cols, kind="mergesort")
    ordered = ordered.reindex(columns=sorted(ordered.columns)).reset_index(drop=True)
    raw = ordered.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_SORT_HINTS = (
    "trade_date",
    "symbol_a",
    "expiry_a",
    "symbol_b",
    "expiry_b",
    "trade_id",
)
