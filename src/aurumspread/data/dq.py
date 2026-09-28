"""Data-quality log (docs/DATA_CONTRACT.md "Validation": each failure is logged)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

Stage = Literal["fetch", "parse", "validate", "registry"]


@dataclass(frozen=True)
class DQEvent:
    """One data-quality finding. ``key`` is ``"SYMBOL/EXPIRY"`` when row-specific."""

    trade_date: date | None
    stage: Stage
    code: str
    message: str
    key: str | None = None


@dataclass
class DQLog:
    """Append-only collection of :class:`DQEvent`; convertible to a DataFrame for export."""

    events: list[DQEvent] = field(default_factory=list)

    def add(
        self,
        trade_date: date | None,
        stage: Stage,
        code: str,
        message: str,
        key: str | None = None,
    ) -> DQEvent:
        event = DQEvent(trade_date, stage, code, message, key)
        self.events.append(event)
        return event

    def codes(self) -> list[str]:
        return [e.code for e in self.events]

    def __len__(self) -> int:
        return len(self.events)

    def to_frame(self) -> pd.DataFrame:
        columns = ["trade_date", "stage", "code", "message", "key"]
        if not self.events:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([e.__dict__ for e in self.events], columns=columns)
