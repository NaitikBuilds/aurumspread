"""T08 cost model. Fixture rates are synthetic, not MCX or broker figures.

Hand arithmetic for one buy of notional 10_000 with tick size 0.5, 1 tick,
brokerage 10, exchange/sebi 1 per 100, stamp 4 per 100, CTT 2 per 100, GST 10
per 100 of (brokerage + exchange + SEBI):

    brokerage 10, exchange 100, ctt 0, sebi 100, stamp 400,
    gst 0.10 * (10 + 100 + 100) = 21, slippage 0.5
    total 631.5
"""

from __future__ import annotations

import pytest

from aurumspread.backtest.costs import estimate_round_trip_cost, order_cost_inr
from aurumspread.config import load_contracts, load_costs

NOTIONAL_A = 10_000.0
NOTIONAL_B = 2_000.0
QTY_G = 100.0


def _fixture_configs():
    costs = load_costs().model_copy(
        update={
            "brokerage_inr_per_order": 10.0,
            "exchange_txn_charge_pct": 1.0,
            "ctt_pct_on_sell": 2.0,
            "sebi_fee_pct": 1.0,
            "stamp_duty_pct": 4.0,
            "gst_pct_on_fees": 10.0,
        }
    )
    contracts = load_contracts()
    updated = {
        symbol: contracts.spec(symbol).model_copy(update={"tick_size_inr": 0.5})
        for symbol in ("GOLDM", "GOLDTEN")
    }
    contracts = contracts.model_copy(update={"contracts": {**contracts.contracts, **updated}})
    return costs, contracts


def test_buy_and_sell_match_hand_arithmetic() -> None:
    costs, contracts = _fixture_configs()
    buy = order_cost_inr("GOLDM", "buy", NOTIONAL_A, thin=False, costs=costs, contracts=contracts)
    sell = order_cost_inr("GOLDM", "sell", NOTIONAL_A, thin=False, costs=costs, contracts=contracts)
    assert buy.missing == ()
    assert buy.total_inr == pytest.approx(631.5)
    assert buy.ctt_inr == pytest.approx(0.0)
    assert buy.stamp_inr == pytest.approx(400.0)
    assert sell.total_inr == pytest.approx(431.5)
    assert sell.ctt_inr == pytest.approx(200.0)
    assert sell.stamp_inr == pytest.approx(0.0)
    assert sell.gst_inr == pytest.approx(21.0)


def test_round_trip_is_four_fills_and_monotonic() -> None:
    costs, contracts = _fixture_configs()
    kwargs = dict(
        symbol_a="GOLDM",
        symbol_b="GOLDTEN",
        traded_value_a_inr=NOTIONAL_A,
        traded_value_b_inr=NOTIONAL_B,
        qty_g=QTY_G,
        thin_a=False,
        thin_b=False,
        costs=costs,
        contracts=contracts,
    )
    half = estimate_round_trip_cost(**kwargs, multiplier=0.5)
    base = estimate_round_trip_cost(**kwargs, multiplier=1.0)
    double = estimate_round_trip_cost(**kwargs, multiplier=2.0)
    # buy A 631.5 + sell B 96 + sell A 431.5 + buy B 136 = 1295
    assert base.total_inr == pytest.approx(1295.0)
    assert base.cost_inr_per_g == pytest.approx(12.95)
    assert half.total_inr == pytest.approx(1295.0 * 0.5)
    assert double.total_inr == pytest.approx(1295.0 * 2.0)
    assert half.total_inr < base.total_inr < double.total_inr


def test_thin_day_doubles_that_legs_slippage_only() -> None:
    costs, contracts = _fixture_configs()
    common = dict(
        symbol_a="GOLDM",
        symbol_b="GOLDTEN",
        traded_value_a_inr=NOTIONAL_A,
        traded_value_b_inr=NOTIONAL_B,
        qty_g=QTY_G,
        thin_b=False,
        costs=costs,
        contracts=contracts,
    )
    plain = estimate_round_trip_cost(**common, thin_a=False)
    thin = estimate_round_trip_cost(**common, thin_a=True)
    # GOLDM: 1 tick * 0.5 INR * two fills. Thin multiplier 2 adds 0.5 per fill.
    assert thin.slippage_inr == pytest.approx(plain.slippage_inr + 1.0)
    assert thin.brokerage_inr == pytest.approx(plain.brokerage_inr)


def test_repo_config_does_not_invent_a_cost() -> None:
    cost = estimate_round_trip_cost(
        "GOLDM",
        "GOLDTEN",
        NOTIONAL_A,
        NOTIONAL_B,
        QTY_G,
        thin_a=False,
        thin_b=False,
        costs=load_costs(),
        contracts=load_contracts(),
    )
    assert cost.total_inr is None
    assert cost.cost_inr_per_g is None
    assert "brokerage_inr_per_order" in cost.missing
    assert "tick_size_inr:GOLDM" in cost.missing
    assert "tick_size_inr:GOLDTEN" in cost.missing


def test_null_tick_is_not_zero_slippage() -> None:
    costs, contracts = _fixture_configs()
    contracts = contracts.model_copy(
        update={
            "contracts": {
                **contracts.contracts,
                "GOLDM": contracts.spec("GOLDM").model_copy(update={"tick_size_inr": None}),
            }
        }
    )
    order = order_cost_inr("GOLDM", "buy", NOTIONAL_A, thin=False, costs=costs, contracts=contracts)
    assert order.slippage_inr is None
    assert order.total_inr is None
    assert order.missing == ("tick_size_inr:GOLDM",)
