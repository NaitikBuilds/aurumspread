"""Run manifest for a walk-forward result (BACKTEST_PROTOCOL, FR-9).

The manifest is a pure function of the inputs. It has no wall-clock time, so
two runs on the same frames and config produce the same hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from aurumspread.config import BacktestConfig, ContractsConfig, CostsConfig


@dataclass(frozen=True)
class RunManifest:
    """Identity of one engine run. No clock and no git object."""

    config_sha256: str
    inputs_sha256: str
    start_date: date | None
    end_date: date | None
    seed: int
    n_trades: int
    n_skips: int
    skip_counts: tuple[tuple[str, int], ...]
    split_frozen: bool


@dataclass(frozen=True)
class RunStamp:
    """Git SHA and wall clock from the I/O edge. The engine never builds this."""

    git_commit: str | None
    recorded_at: datetime | None


def stamp_run(
    *, git_commit: str | None = None, recorded_at: datetime | None = None
) -> RunStamp:
    """Attach identity the caller already has. Does not read git or the clock."""
    return RunStamp(git_commit=git_commit, recorded_at=recorded_at)


def config_sha256(
    backtest: BacktestConfig,
    costs: CostsConfig,
    contracts: ContractsConfig,
    *,
    target_g: float,
    multiplier: float,
    allow_test: bool,
    calendar_dates: tuple[date, ...] = (),
) -> str:
    """SHA-256 of the config objects and the run arguments that affect trades."""
    payload = {
        "allow_test": allow_test,
        "backtest": backtest.model_dump(mode="json"),
        "calendar": [day.isoformat() for day in calendar_dates],
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
