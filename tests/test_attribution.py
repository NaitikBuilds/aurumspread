"""Tests for daily attribution (PRD Section 8, T10).

Verifies the mathematical identity:
    total_pnl_inr = beta_inr + alpha_inr + cost_inr + residual_inr
along with reference gold tracking, carry subset reporting, hand-computed numbers,
and edge cases.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from aurumspread.backtest import (
    ATTRIBUTION_COLUMNS,
    compute_daily_attribution,
    flag_residual_outliers,
    verify_attribution_identity,
    walk_forward,
)
from aurumspread.backtest.costs import order_cost_inr
from aurumspread.config import load_backtest, load_contracts, load_costs
from aurumspread.core.calendar import TradingCalendar

EXPIRY_A = date(2026, 2, 5)
EXPIRY_B = date(2026, 1, 30)
DAYS = [date(2026, 1, day) for day in range(1, 7)]


def _configs(warmup_days: int = 0):
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
        symbol: contracts.spec(symbol).model_copy(
            update={"tick_size_inr": 0.5, "lot_size_units": 1}
        )
        for symbol in ("GOLDM", "GOLDTEN")
    }
    contracts = contracts.model_copy(update={"contracts": {**contracts.contracts, **updated}})
    backtest = load_backtest().model_copy(
        update={"split": load_backtest().split.model_copy(update={"warmup_days": warmup_days})}
    )
    return backtest, costs, contracts


def _frame(
    zscores: list[float] | None = None,
    price_a_exit: float = 12.0,
    carry_b: float | None = None,
) -> pd.DataFrame:
    zscores = zscores or [0.0, 0.0, 3.0, 3.0, 0.2, 0.0]
    rows = []
    for day, zscore in zip(DAYS, zscores, strict=True):
        price_a = price_a_exit if day == DAYS[5] else 10.0
        rows.append(
            {
                "trade_date": day,
                "symbol_a": "GOLDM",
                "expiry_a": EXPIRY_A,
                "symbol_b": "GOLDTEN",
                "expiry_b": EXPIRY_B,
                "zscore": zscore,
                "price_a_inr_per_g": price_a,
                "price_b_inr_per_g": 10.0,
                "volume_a": 10.0,
                "open_interest_a": 10.0,
                "volume_b": 10.0,
                "open_interest_b": 10.0,
                "thin_a": False,
                "thin_b": False,
                "status_a": "live",
                "status_b": "live",
                "carry_b_inr_per_g_per_day": carry_b if carry_b is not None else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _run(frame: pd.DataFrame | None = None, **kwargs):
    backtest, costs, contracts = _configs()
    return walk_forward(
        frame if frame is not None else _frame(),
        backtest=backtest,
        costs=costs,
        contracts=contracts,
        target_g=100.0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Core Mathematical Identity & Cumulative Match
# ---------------------------------------------------------------------------


def test_attribution_identity_holds_for_walk_forward_result() -> None:
    result = _run()
    attr = result.attribution
    assert not attr.empty
    assert verify_attribution_identity(attr)

    for _, row in attr.iterrows():
        expected_total = row["beta_inr"] + row["alpha_inr"] + row["cost_inr"] + row["residual_inr"]
        assert row["total_pnl_inr"] == pytest.approx(expected_total, abs=1e-9)
        assert row["residual_inr"] == pytest.approx(0.0, abs=1e-9)


def test_cumulative_attribution_matches_trade_totals() -> None:
    result = _run()
    attr = result.attribution
    trade = result.trades.iloc[0]

    # Gross P&L match: sum(beta + alpha) == trade.gross_pnl_inr
    total_gross = (attr["beta_inr"] + attr["alpha_inr"]).sum()
    assert total_gross == pytest.approx(trade["gross_pnl_inr"])

    # Cost match: sum(cost_inr) == -trade.cost_inr (costs are negative in attribution)
    assert attr["cost_inr"].sum() == pytest.approx(-trade["cost_inr"])

    # Net P&L match: sum(total_pnl_inr) == trade.net_pnl_inr
    assert attr["total_pnl_inr"].sum() == pytest.approx(trade["net_pnl_inr"])


# ---------------------------------------------------------------------------
# 2. Hand-Computed Day-by-Day Numbers
# ---------------------------------------------------------------------------


def test_hand_computed_daily_attribution_breakdown() -> None:
    """Trade fills on Jan 4, held Jan 5, exits Jan 6.

    Prices:
    - Entry 4 Jan: P_A=10, P_B=10
    - Hold 5 Jan: P_A=10, P_B=10
    - Exit 6 Jan: P_A=12, P_B=10
    Quantities: short spread -> qty_a = -100 g, qty_b = +100 g.

    Day 4 (Entry):
      gross = -100 * (10 - 10) + 100 * (10 - 10) = 0
      cost = -entry_fee
      total = -entry_fee
    Day 5 (Hold):
      gross = -100 * (10 - 10) + 100 * (10 - 10) = 0
      cost = 0
      total = 0
    Day 6 (Exit):
      gross = -100 * (12 - 10) + 100 * (10 - 10) = -200
      cost = -exit_fee
      total = -200 - exit_fee
    """
    result = _run()
    attr = result.attribution.set_index("trade_date")

    assert list(attr.index) == [DAYS[3], DAYS[4], DAYS[5]]

    # Day 4: Entry session
    day4 = attr.loc[DAYS[3]]
    assert day4["beta_inr"] == pytest.approx(0.0)
    assert day4["alpha_inr"] == pytest.approx(0.0)
    assert day4["cost_inr"] < 0
    assert day4["total_pnl_inr"] == pytest.approx(day4["cost_inr"])
    assert day4["residual_inr"] == pytest.approx(0.0)

    # Day 5: Holding session
    day5 = attr.loc[DAYS[4]]
    assert day5["beta_inr"] == pytest.approx(0.0)
    assert day5["alpha_inr"] == pytest.approx(0.0)
    assert day5["cost_inr"] == pytest.approx(0.0)
    assert day5["total_pnl_inr"] == pytest.approx(0.0)
    assert day5["residual_inr"] == pytest.approx(0.0)

    # Day 6: Exit session
    day6 = attr.loc[DAYS[5]]
    assert day6["beta_inr"] == pytest.approx(0.0)
    assert day6["alpha_inr"] == pytest.approx(-200.0)
    assert day6["cost_inr"] < 0
    assert day6["total_pnl_inr"] == pytest.approx(-200.0 + day6["cost_inr"])
    assert day6["residual_inr"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. Reference Gold Tracking (dRef) and Beta/Alpha Separation
# ---------------------------------------------------------------------------


def test_d_ref_constant_separates_beta_and_alpha() -> None:
    # With target_g = 100g on balanced pair, residual_g is 0, so beta is 0 regardless of d_ref
    result_zero = _run(d_ref=0.0)
    result_five = _run(d_ref=5.0)

    # Gross P&L (beta + alpha) must be strictly identical
    pd.testing.assert_series_equal(
        result_zero.attribution["total_pnl_inr"],
        result_five.attribution["total_pnl_inr"],
    )
    pd.testing.assert_series_equal(
        result_zero.attribution["alpha_inr"],
        result_five.attribution["alpha_inr"],
    )


def test_unhedged_residual_grams_produce_non_zero_beta() -> None:
    """When residual grams != 0, beta = residual_g * dRef and alpha = gross - beta."""
    frame = _frame()
    backtest, costs, contracts = _configs()
    wf = walk_forward(
        frame,
        backtest=backtest,
        costs=costs,
        contracts=contracts,
        target_g=100.0,
    )
    trades = wf.trades.copy()
    # Artificially set an unhedged residual: -100g of A and +90g of B -> residual = -10g
    trades["qty_g_b"] = 90.0
    trades["residual_g"] = -10.0

    d_ref_val = 2.5
    attr = compute_daily_attribution(
        trades,
        frame,
        d_ref=d_ref_val,
        costs=costs,
        contracts=contracts,
    )

    assert verify_attribution_identity(attr)
    # On day 6, price moved:
    # dP_A = +2.0, dP_B = 0.0
    # gross = -100 * 2.0 + 90 * 0.0 = -200.0
    # beta = -10 * 2.5 = -25.0
    # alpha = -100 * (2.0 - 2.5) + 90 * (0.0 - 2.5)
    #       = -100 * (-0.5) + 90 * (-2.5) = 50 - 225 = -175.0
    # beta + alpha = -25 + (-175) = -200.0 == gross!
    day6 = attr.set_index("trade_date").loc[DAYS[5]]
    assert day6["beta_inr"] == pytest.approx(-25.0)
    assert day6["alpha_inr"] == pytest.approx(-175.0)
    assert day6["beta_inr"] + day6["alpha_inr"] == pytest.approx(-200.0)


def test_d_ref_dict_series_and_symbol_modes() -> None:
    frame = _frame()
    backtest, costs, contracts = _configs()
    wf = walk_forward(frame, backtest=backtest, costs=costs, contracts=contracts, target_g=100.0)

    # 1. Dict mapping
    d_map = {DAYS[3]: 1.0, DAYS[4]: -0.5, DAYS[5]: 2.0}
    attr_dict = compute_daily_attribution(
        wf.trades, frame, d_ref=d_map, costs=costs, contracts=contracts
    )
    assert verify_attribution_identity(attr_dict)

    # 2. Pandas Series
    s_ref = pd.Series(d_map)
    attr_series = compute_daily_attribution(
        wf.trades, frame, d_ref=s_ref, costs=costs, contracts=contracts
    )
    assert verify_attribution_identity(attr_series)
    pd.testing.assert_frame_equal(attr_dict, attr_series)

    # 3. Symbol reference ("GOLDM")
    attr_sym = compute_daily_attribution(
        wf.trades, frame, d_ref="GOLDM", costs=costs, contracts=contracts
    )
    assert verify_attribution_identity(attr_sym)


# ---------------------------------------------------------------------------
# 4. Carry Subset Reporting
# ---------------------------------------------------------------------------


def test_carry_inr_reported_as_subset_of_alpha() -> None:
    carry_rate = 0.05  # INR/g/day
    frame = _frame(carry_b=carry_rate)
    result = _run(frame)
    attr = result.attribution.set_index("trade_date")

    # Long leg B (+100g) earns carry_rate * 100g * calendar_days
    # Day 5 (1 day after Jan 4): 100 * 0.05 * 1 = 5.0 INR
    assert attr.loc[DAYS[4], "carry_inr"] == pytest.approx(5.0)
    # Day 6 (1 day after Jan 5): 100 * 0.05 * 1 = 5.0 INR
    assert attr.loc[DAYS[5], "carry_inr"] == pytest.approx(5.0)

    # Carry is a subset of alpha and not added again to total_pnl_inr:
    assert verify_attribution_identity(result.attribution)


# ---------------------------------------------------------------------------
# 5. Edge Cases
# ---------------------------------------------------------------------------


def test_empty_trades_produces_empty_attribution_frame() -> None:
    # Liquidity filter causes 0 trades
    backtest, costs, contracts = _configs()
    liquid = backtest.model_copy(
        update={"liquidity": backtest.liquidity.model_copy(update={"min_volume_lots": 100})}
    )
    result = walk_forward(
        _frame(), backtest=liquid, costs=costs, contracts=contracts, target_g=100.0
    )
    assert result.trades.empty
    attr = result.attribution
    assert attr.empty
    assert list(attr.columns) == list(ATTRIBUTION_COLUMNS)
    assert attr["total_pnl_inr"].dtype == "float64"


def test_zero_pnl_trade_identity() -> None:
    # Price never moves (price_a_exit = 10.0)
    frame = _frame(price_a_exit=10.0)
    result = _run(frame)
    trade = result.trades.iloc[0]
    assert trade["gross_pnl_inr"] == pytest.approx(0.0)

    attr = result.attribution
    assert verify_attribution_identity(attr)
    assert (attr["beta_inr"] + attr["alpha_inr"]).sum() == pytest.approx(0.0)
    assert attr["total_pnl_inr"].sum() == pytest.approx(-trade["cost_inr"])


def test_same_day_entry_and_exit_attribution() -> None:
    """Exit buffer triggers exit on the same day as fill."""
    days = [date(2026, 1, day) for day in range(1, 11)]
    expiry_b = date(2026, 1, 12)
    # Buffer is 5 days before 12 Jan -> 7 Jan is exit buffer
    zscores = [0.0, 0.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]
    rows = [
        {
            "trade_date": day,
            "symbol_a": "GOLDM",
            "expiry_a": EXPIRY_A,
            "symbol_b": "GOLDTEN",
            "expiry_b": expiry_b,
            "zscore": z,
            "price_a_inr_per_g": 10.0,
            "price_b_inr_per_g": 10.0,
            "volume_a": 10.0,
            "open_interest_a": 10.0,
            "volume_b": 10.0,
            "open_interest_b": 10.0,
            "thin_a": False,
            "thin_b": False,
            "status_a": "live",
            "status_b": "live",
        }
        for day, z in zip(days, zscores, strict=True)
    ]
    frame = pd.DataFrame(rows)
    result = _run(frame)
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "exit_buffer"

    attr = result.attribution
    assert verify_attribution_identity(attr)
    assert attr["total_pnl_inr"].sum() == pytest.approx(result.trades["net_pnl_inr"].sum())


def test_include_idle_sessions() -> None:
    result = _run()
    calendar = TradingCalendar(DAYS)
    backtest, costs, contracts = _configs()
    attr_idle = compute_daily_attribution(
        result.trades,
        _frame(),
        calendar=calendar,
        costs=costs,
        contracts=contracts,
        include_idle=True,
    )
    assert len(attr_idle) == len(DAYS)
    # Jan 1, 2, 3 have no active position
    for d in (DAYS[0], DAYS[1], DAYS[2]):
        row = attr_idle.set_index("trade_date").loc[d]
        assert row["total_pnl_inr"] == pytest.approx(0.0)
    assert verify_attribution_identity(attr_idle)


def test_standalone_runner_accepts_walk_forward_result() -> None:
    result = _run()
    # 1. Directly from WalkForwardResult
    attr_direct = compute_daily_attribution(result)
    assert verify_attribution_identity(attr_direct)
    pd.testing.assert_frame_equal(result.attribution, attr_direct)

    # 2. From trades DataFrame and signals frame with configs
    backtest, costs, contracts = _configs()
    attr_recomputed = compute_daily_attribution(
        result.trades,
        _frame(),
        costs=costs,
        contracts=contracts,
    )
    assert verify_attribution_identity(attr_recomputed)
    pd.testing.assert_frame_equal(result.attribution, attr_recomputed)


# ---------------------------------------------------------------------------
# 6. Schema and Types
# ---------------------------------------------------------------------------


def test_attribution_columns_and_types() -> None:
    result = _run()
    attr = result.attribution
    assert tuple(attr.columns) == ATTRIBUTION_COLUMNS
    for d in attr["trade_date"]:
        assert isinstance(d, date)
        assert not isinstance(d, datetime)

    for col in (
        "beta_inr",
        "alpha_inr",
        "carry_inr",
        "cost_inr",
        "residual_inr",
        "total_pnl_inr",
    ):
        assert attr[col].dtype == "float64"


# ---------------------------------------------------------------------------
# 7. Residual Tolerance and T08 Cost Model Validation
# ---------------------------------------------------------------------------


def test_cost_inr_per_session_equals_t08_order_costs() -> None:
    """Validate that daily cost_inr matches the T08 order_cost_inr model exactly."""
    result = _run()
    backtest, costs, contracts = _configs()
    attr = result.attribution.set_index("trade_date")

    # Short spread: entry sells A and buys B at 10 INR/g; exit buys A at 12 and sells B at 10 INR/g
    entry_cost_a = order_cost_inr(
        "GOLDM", "sell", 100.0 * 10.0, thin=False, costs=costs, contracts=contracts
    )
    entry_cost_b = order_cost_inr(
        "GOLDTEN", "buy", 100.0 * 10.0, thin=False, costs=costs, contracts=contracts
    )
    expected_entry_total = entry_cost_a.total_inr + entry_cost_b.total_inr

    exit_cost_a = order_cost_inr(
        "GOLDM", "buy", 100.0 * 12.0, thin=False, costs=costs, contracts=contracts
    )
    exit_cost_b = order_cost_inr(
        "GOLDTEN", "sell", 100.0 * 10.0, thin=False, costs=costs, contracts=contracts
    )
    expected_exit_total = exit_cost_a.total_inr + exit_cost_b.total_inr

    # Day 4 (entry fill session): cost_inr must equal -expected_entry_total
    assert attr.loc[DAYS[3], "cost_inr"] == pytest.approx(-expected_entry_total)

    # Day 5 (holding session): no orders, cost_inr must be 0.0
    assert attr.loc[DAYS[4], "cost_inr"] == pytest.approx(0.0)

    # Day 6 (exit fill session): cost_inr must equal -expected_exit_total
    assert attr.loc[DAYS[5], "cost_inr"] == pytest.approx(-expected_exit_total)


def test_non_zero_residual_from_fill_rounding_discrepancy() -> None:
    """Hand-computed fixture with a small non-zero residual due to fill rounding.

    Suppose execution entry fill on Day 4 occurred at 10.005 INR/g, while the
    daily signal bar is rounded to 10.00 INR/g.
    For short 100g of Leg A:
      Actual MTM gross on Day 4: -100 * (10.00 - 10.005) = +0.50 INR.
      Model signal gross on Day 4: -100 * (10.00 - 10.00) = 0.00 INR.
      Tracking error residual_inr = +0.50 INR.
    """
    result = _run()
    trades_mod = result.trades.copy()
    # Introduce a 0.005 INR/g execution fill difference on leg A
    trades_mod.loc[0, "entry_fill_a_inr_per_g"] = 10.005

    backtest, costs, contracts = _configs()
    attr = compute_daily_attribution(
        trades_mod,
        _frame(),
        costs=costs,
        contracts=contracts,
    )

    day4 = attr.set_index("trade_date").loc[DAYS[3]]
    # Assert exact hand-computed residual value (+0.50 INR)
    assert day4["residual_inr"] == pytest.approx(0.50, abs=1e-6)
    # Core identity total_pnl_inr == beta_inr + alpha_inr + cost_inr + residual_inr holds!
    assert verify_attribution_identity(attr)


def test_flag_residual_outliers_and_backtest_yaml_tolerance() -> None:
    """Verify residual tolerance in backtest.yaml and outlier flagging."""
    cfg = load_backtest()
    assert hasattr(cfg, "residual_tolerance_inr")
    assert cfg.residual_tolerance_inr == 0.01

    result = _run()
    trades_mod = result.trades.copy()
    # 0.005 INR/g fill difference -> 0.50 INR residual on 100g
    trades_mod.loc[0, "entry_fill_a_inr_per_g"] = 10.005

    backtest, costs, contracts = _configs()
    attr = compute_daily_attribution(
        trades_mod,
        _frame(),
        costs=costs,
        contracts=contracts,
    )

    # With default tolerance 0.01: Day 4 has |0.50| > 0.01, so it is flagged
    flagged = flag_residual_outliers(attr, tolerance=cfg.residual_tolerance_inr)
    assert len(flagged) == 1
    assert flagged.iloc[0]["trade_date"] == DAYS[3]
    assert flagged.iloc[0]["residual_inr"] == pytest.approx(0.50)

    # With wide tolerance 1.0: |0.50| <= 1.0, no days are flagged
    not_flagged = flag_residual_outliers(attr, tolerance=1.0)
    assert not_flagged.empty
