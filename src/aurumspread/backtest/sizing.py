"""Gram-matched lot sizing (PRD section 7, BACKTEST_PROTOCOL rule 6).

Each leg is an integer number of lots. Grams per lot are
``trading_unit_g * lot_size_units`` from ``config/contracts.yaml``.
``lot_size_units`` is ``verify:true`` and null in the repo config; this
function then returns an unsized result instead of assuming 1.

The leg with the larger lot (in grams) is the anchor: ``floor(target_g / lot)``.
The other leg is floored to that anchor. The gram gap is ``residual_g``
(signed long grams plus short grams). GOLDGUINEA's 8 g unit is what leaves
a residual against a 100 g GOLDM lot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from aurumspread.config import ContractsConfig

SpreadSide = Literal["long_spread", "short_spread"]


@dataclass(frozen=True)
class SizedPair:
    """Lot counts for one ordered pair. Grams are signed: long positive, short negative."""

    symbol_a: str
    symbol_b: str
    side: SpreadSide
    lots_a: int | None
    lots_b: int | None
    qty_g_a: float | None
    qty_g_b: float | None
    residual_g: float | None
    missing: tuple[str, ...]

    @property
    def sized(self) -> bool:
        return not self.missing


def size_pair(
    symbol_a: str,
    symbol_b: str,
    target_g: float,
    contracts: ContractsConfig,
    *,
    side: SpreadSide = "long_spread",
) -> SizedPair:
    """Size a gram-matched pair to ``target_g`` using configured lot increments.

    ``target_g`` is the desired absolute grams before lot rounding. Participation
    caps are not applied here; volume units are still unverified
    (docs/DATA_CONTRACT.md). Use :func:`aurumspread.backtest.liquidity.cap_to_participation`
    once both quantities are in one unit.
    """
    if side not in ("long_spread", "short_spread"):
        raise ValueError(f"side must be 'long_spread' or 'short_spread', got {side!r}")
    if isinstance(target_g, bool) or not isinstance(target_g, (int, float)) or target_g <= 0:
        raise ValueError(f"target_g must be > 0, got {target_g!r}")

    missing: list[str] = []
    increment_a = _grams_per_lot(symbol_a, contracts, missing)
    increment_b = _grams_per_lot(symbol_b, contracts, missing)
    if increment_a is None or increment_b is None:
        return SizedPair(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            side=side,
            lots_a=None,
            lots_b=None,
            qty_g_a=None,
            qty_g_b=None,
            residual_g=None,
            missing=tuple(sorted(set(missing))),
        )

    if increment_a >= increment_b:
        lots_a = _lots(target_g, increment_a)
        grams_a = lots_a * increment_a
        lots_b = _lots(grams_a, increment_b)
        grams_b = lots_b * increment_b
    else:
        lots_b = _lots(target_g, increment_b)
        grams_b = lots_b * increment_b
        lots_a = _lots(grams_b, increment_a)
        grams_a = lots_a * increment_a

    sign_a = 1.0 if side == "long_spread" else -1.0
    sign_b = -sign_a
    qty_g_a = sign_a * grams_a
    qty_g_b = sign_b * grams_b
    return SizedPair(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        side=side,
        lots_a=lots_a,
        lots_b=lots_b,
        qty_g_a=qty_g_a,
        qty_g_b=qty_g_b,
        residual_g=qty_g_a + qty_g_b,
        missing=(),
    )


def _grams_per_lot(symbol: str, contracts: ContractsConfig, missing: list[str]) -> float | None:
    spec = contracts.spec(symbol)
    if spec.lot_size_units is None:
        missing.append(f"lot_size_units:{symbol}")
        return None
    return float(spec.trading_unit_g * spec.lot_size_units)


def _lots(target_g: float, increment_g: float) -> int:
    if increment_g <= 0:
        raise ValueError(f"lot increment must be > 0, got {increment_g}")
    if target_g <= 0:
        return 0
    return int(math.floor(target_g / increment_g))
