"""T08 liquidity filter. Thresholds are synthetic; volume units are not interpreted."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from aurumspread.backtest.liquidity import cap_to_participation, filter_liquid, passes_liquidity
from aurumspread.config import load_backtest


def _liquidity(**overrides: object):
    base = load_backtest().liquidity
    return base.model_copy(update=overrides)


def test_null_floors_still_reject_a_dead_book() -> None:
    decision = passes_liquidity(0.0, 10.0, _liquidity())
    assert decision.passed is False
    assert decision.floors_active is False
    assert decision.reasons == ("volume_not_positive",)


def test_positive_book_passes_while_floors_are_off() -> None:
    decision = passes_liquidity(10.0, 4.0, load_backtest().liquidity)
    assert decision.passed is True
    assert decision.reasons == ()
    assert decision.floors_active is False


def test_configured_floor_rejects_smaller_raw_volume() -> None:
    liquidity = _liquidity(min_volume_lots=11, min_open_interest_lots=20)
    decision = passes_liquidity(10.0, 20.0, liquidity)
    assert decision.passed is False
    assert decision.floors_active is True
    assert decision.reasons == ("below_min_volume",)


def test_filter_liquid_keeps_only_passing_rows() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["GOLDM", "GOLDTEN", "GOLDPETAL"],
            "volume": [10.0, math.nan, 3.0],
            "open_interest": [10.0, 10.0, 1.0],
        }
    )
    kept = filter_liquid(frame, _liquidity(min_volume_lots=5))
    assert kept["symbol"].tolist() == ["GOLDM"]
    assert frame["symbol"].tolist() == ["GOLDM", "GOLDTEN", "GOLDPETAL"]


def test_participation_cap_scales_in_the_callers_unit() -> None:
    # 5% of 100 = 5. An order of 10 is cut to 5; an order of 4 is unchanged.
    assert cap_to_participation(10.0, 100.0, 5.0) == pytest.approx(5.0)
    assert cap_to_participation(4.0, 100.0, 5.0) == pytest.approx(4.0)
    assert cap_to_participation(10.0, 0.0, 5.0) == 0.0
