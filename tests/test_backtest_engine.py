"""T09 walk-forward. Prices and fee rates are synthetic, not market or MCX data.

Day t=2026-01-03 has z=3, so the engine shorts the spread and fills on
2026-01-04. z=0.2 on 2026-01-05 exits, filled on 2026-01-06.

Leg A falls from the entry print only at the exit fill: 10 -> 12 INR/g.
Short 100 g of A and long 100 g of B (B unchanged) is gross P&L
-100 * (12 - 10) = -200 INR.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aurumspread.backtest.costs import order_cost_inr
from aurumspread.backtest.engine import walk_forward
from aurumspread.backtest.manifest import stamp_run
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


def _frame(zscores: list[float] | None = None, price_a_exit: float = 12.0) -> pd.DataFrame:
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


def test_short_spread_fills_next_session_and_gross_is_hand_computed() -> None:
    result = _run()
    assert len(result.trades) == 1
    trade = result.trades.iloc[0]
    assert trade["signal_date"] == DAYS[2]
    assert trade["entry_fill_date"] == DAYS[3]
    assert trade["exit_signal_date"] == DAYS[4]
    assert trade["exit_fill_date"] == DAYS[5]
    assert trade["side"] == "short_spread"
    assert trade["exit_reason"] == "exit_z"
    assert trade["qty_g_a"] == -100.0
    assert trade["qty_g_b"] == 100.0
    assert trade["residual_g"] == 0.0
    assert trade["gross_pnl_inr"] == pytest.approx(-200.0)
    assert trade["net_pnl_inr"] == pytest.approx(trade["gross_pnl_inr"] - trade["cost_inr"])
    assert trade["cost_inr"] > 0


def test_cost_matches_four_order_costs() -> None:
    backtest, costs, contracts = _configs()
    result = walk_forward(
        _frame(),
        backtest=backtest,
        costs=costs,
        contracts=contracts,
        target_g=100.0,
    )
    trade = result.trades.iloc[0]
    # short: sell A / buy B at entry prices 10, then buy A at 12 and sell B at 10.
    fills = (
        ("sell", "GOLDM", 100.0 * 10.0, False),
        ("buy", "GOLDTEN", 100.0 * 10.0, False),
        ("buy", "GOLDM", 100.0 * 12.0, False),
        ("sell", "GOLDTEN", 100.0 * 10.0, False),
    )
    expected = 0.0
    for side, symbol, notional, thin in fills:
        order = order_cost_inr(symbol, side, notional, thin=thin, costs=costs, contracts=contracts)
        assert order.total_inr is not None
        expected += order.total_inr
    assert trade["cost_inr"] == pytest.approx(expected)


def test_two_runs_match_including_the_manifest() -> None:
    first = _run()
    second = _run()
    pd.testing.assert_frame_equal(first.trades, second.trades)
    pd.testing.assert_frame_equal(first.skips, second.skips)
    pd.testing.assert_frame_equal(first.attribution, second.attribution)
    assert first.manifest == second.manifest
    assert first.manifest.split_frozen is False
    assert first.manifest.n_trades == 1
    assert not hasattr(first.manifest, "git_commit")
    assert walk_forward.__code__.co_varnames.count("git_commit") == 0
    stamped = stamp_run(git_commit="abc123", recorded_at=None)
    assert stamped.git_commit == "abc123"
    assert stamped.recorded_at is None


def test_later_zscore_does_not_change_a_fill_already_decided() -> None:
    baseline = _run()
    shifted = _frame()
    shifted.loc[shifted["trade_date"] == DAYS[5], "zscore"] = 50.0
    again = _run(shifted)
    pd.testing.assert_frame_equal(baseline.trades, again.trades)
    assert baseline.manifest.inputs_sha256 != again.manifest.inputs_sha256


def test_unverified_repo_costs_skip_the_entry() -> None:
    backtest, _, contracts = _configs()
    result = walk_forward(
        _frame(),
        backtest=backtest,
        costs=load_costs(),
        contracts=contracts,
        target_g=100.0,
    )
    assert result.trades.empty
    # z stays at 3 on the signal day and the next session, so both attempts skip.
    assert result.skips["reason"].tolist() == ["unverified_costs", "unverified_costs"]
    assert result.skip_counts == {"unverified_costs": 2}
    assert result.manifest.n_skips == 2
    assert result.manifest.skip_counts == (("unverified_costs", 2),)
    assert result.manifest.n_trades == 0


def test_allow_test_requires_a_frozen_split() -> None:
    with pytest.raises(ValueError, match="allow_test"):
        _run(allow_test=True)


def test_exit_buffer_fills_the_same_session_from_dte_not_prices() -> None:
    # buffer=5, expiry_b=12 Jan: first buffer day is 7 Jan. Signal 3 Jan, fill 4 Jan.
    days = [date(2026, 1, day) for day in range(1, 11)]
    expiry_b = date(2026, 1, 12)
    zscores = [0.0, 0.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]
    rows = []
    for day, zscore in zip(days, zscores, strict=True):
        rows.append(
            {
                "trade_date": day,
                "symbol_a": "GOLDM",
                "expiry_a": EXPIRY_A,
                "symbol_b": "GOLDTEN",
                "expiry_b": expiry_b,
                "zscore": zscore,
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
        )
    frame = pd.DataFrame(rows)
    baseline = _run(frame)
    trade = baseline.trades.iloc[0]
    assert trade["exit_reason"] == "exit_buffer"
    assert trade["exit_signal_date"] == date(2026, 1, 7)
    assert trade["exit_fill_date"] == date(2026, 1, 7)
    mutated = frame.copy()
    later = mutated["trade_date"] > date(2026, 1, 7)
    mutated.loc[later, "price_a_inr_per_g"] = 99.0
    mutated.loc[later, "zscore"] = 0.0
    again = _run(mutated)
    assert again.trades.iloc[0]["exit_fill_date"] == date(2026, 1, 7)
    assert again.trades.iloc[0]["exit_fill_a_inr_per_g"] == pytest.approx(10.0)


def test_spoofed_status_does_not_same_session_exit() -> None:
    frame = _frame()
    frame.loc[frame["trade_date"] == DAYS[4], "status_a"] = "exit_buffer"
    frame.loc[frame["trade_date"] == DAYS[4], "price_a_inr_per_g"] = 11.0
    result = _run(frame)
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "exit_z"
    assert trade["exit_signal_date"] == DAYS[4]
    assert trade["exit_fill_date"] == DAYS[5]


def test_next_session_skips_holiday_gap() -> None:
    # Fri 2 Jan, then Mon 5 / Tue 6. A timedelta fill would land on Saturday 3.
    sessions = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    calendar = TradingCalendar(sessions)
    z_by_day = {date(2026, 1, 2): 3.0, date(2026, 1, 5): 3.0, date(2026, 1, 6): 0.2}
    rows = [
        {
            "trade_date": day,
            "symbol_a": "GOLDM",
            "expiry_a": EXPIRY_A,
            "symbol_b": "GOLDTEN",
            "expiry_b": EXPIRY_B,
            "zscore": z_by_day[day],
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
        for day in sessions
    ]
    result = _run(pd.DataFrame(rows), calendar=calendar)
    trade = result.trades.iloc[0]
    assert trade["signal_date"] == date(2026, 1, 2)
    assert trade["entry_fill_date"] == date(2026, 1, 5)
    assert trade["entry_fill_date"] != date(2026, 1, 3)


def test_missing_row_does_not_fill_the_next_pair_bar() -> None:
    # Calendar has 4 Jan; the pair frame jumps 3 Jan -> 5 Jan.
    calendar = TradingCalendar(
        [date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)]
    )
    z_by_day = {date(2026, 1, 2): 0.0, date(2026, 1, 3): 3.0, date(2026, 1, 5): 3.0}
    rows = [
        {
            "trade_date": day,
            "symbol_a": "GOLDM",
            "expiry_a": EXPIRY_A,
            "symbol_b": "GOLDTEN",
            "expiry_b": EXPIRY_B,
            "zscore": zscore,
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
        for day, zscore in z_by_day.items()
    ]
    result = _run(pd.DataFrame(rows), calendar=calendar)
    assert result.trades.empty
    assert result.skip_counts["missing_fill_bar"] >= 1
    assert (
        "missing_fill_bar",
        result.skip_counts["missing_fill_bar"],
    ) in result.manifest.skip_counts


def test_skipped_entries_are_counted_by_reason() -> None:
    backtest, costs, contracts = _configs()
    liquid = backtest.model_copy(
        update={
            "liquidity": backtest.liquidity.model_copy(update={"min_volume_lots": 100}),
        }
    )
    result = walk_forward(
        _frame(),
        backtest=liquid,
        costs=costs,
        contracts=contracts,
        target_g=100.0,
    )
    assert result.trades.empty
    assert result.skip_counts == {"liquidity": 2}
    assert result.manifest.skip_counts == (("liquidity", 2),)

    unsized = walk_forward(
        _frame(),
        backtest=backtest,
        costs=costs,
        contracts=load_contracts(),
        target_g=100.0,
    )
    assert unsized.trades.empty
    assert unsized.skip_counts == {"unverified_lots": 2}
    assert unsized.manifest.n_skips == 2
    assert dict(unsized.manifest.skip_counts) == unsized.skip_counts
