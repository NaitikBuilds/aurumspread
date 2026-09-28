"""Hand-computed tests for core.carry (PRD 6.2; acceptance test 7).

Prices are illustrative round numbers chosen so the arithmetic can be checked by
hand in the comments; they are not market data.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from aurumspread.config import AlignmentConfig, load_backtest, load_contracts
from aurumspread.core.carry import (
    PAIR_COLUMNS,
    SPREAD_COL,
    align_pair_on_day,
    build_pair_series,
    choose_nearest_dte_pair,
    constant_maturity_price,
    constant_maturity_table,
    daily_carry_table,
    family_curve,
    implied_carry,
)
from aurumspread.core.lifecycle import annotate_lifecycle
from aurumspread.core.normalize import normalize_prices

D = date(2026, 9, 4)
OCT5, DEC4, FEB3 = date(2026, 10, 5), date(2026, 12, 4), date(2027, 2, 3)  # GOLDM-style
SEP30, OCT30 = date(2026, 9, 30), date(2026, 10, 30)  # 27th-31st families


def _cfg(**overrides: object) -> AlignmentConfig:
    base: dict[str, object] = {
        "carry_method": "front_pair",
        "pooled_carry_fallback": True,
        "max_dte_gap_days": 45,
        "constant_maturity_grid_days": [30, 60, 90],
    }
    base.update(overrides)
    return AlignmentConfig.model_validate(base)


def _row(
    symbol: str,
    expiry: date,
    price: float,
    trade_date: date = D,
    status: str = "live",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "expiry_date": expiry,
        "dte_days": (expiry - trade_date).days,
        "status": status,
        "pure_price_inr_per_g": price,
    }


def _day() -> pd.DataFrame:
    # DTE from 2026-09-04: OCT5=31, DEC4=91, SEP30=26, OCT30=56.
    return pd.DataFrame(
        [
            _row("GOLDM", OCT5, 10040.0),
            _row("GOLDM", DEC4, 10100.0),  # carry_M = (10100-10040)/(91-31) = 1.0 INR/g/day
            _row("GOLDTEN", SEP30, 10050.0),
            _row("GOLDTEN", OCT30, 10080.0),  # carry_T = (10080-10050)/(56-26) = 1.0
            _row("GOLDPETAL", SEP30, 10052.0),  # single expiry -> no own carry
            _row("GOLDM", date(2026, 9, 6), 9000.0, status="exit_buffer"),  # must be ignored
        ]
    )


# ------------------------------------------------------------------ curve + carry


def test_family_curve_uses_live_rows_sorted_by_dte() -> None:
    curve = family_curve(_day(), "GOLDM")
    assert curve["dte_days"].tolist() == [31, 91]  # exit_buffer row (dte 2) excluded
    assert curve["pure_price_inr_per_g"].tolist() == [10040.0, 10100.0]


def test_implied_carry_front_pair_and_median() -> None:
    frame = pd.DataFrame(
        [_row("GOLDM", OCT5, 10040.0), _row("GOLDM", DEC4, 10100.0), _row("GOLDM", FEB3, 10190.0)]
    )
    curve = family_curve(frame, "GOLDM")
    # slopes: (10100-10040)/60 = 1.0 ; (10190-10100)/(152-91) = 90/61
    assert implied_carry(curve, "front_pair") == pytest.approx(1.0)
    assert implied_carry(curve, "adjacent_median") == pytest.approx((1.0 + 90 / 61) / 2)
    assert math.isnan(implied_carry(curve.iloc[:1], "front_pair"))
    with pytest.raises(ValueError, match="carry_method"):
        implied_carry(curve, "magic")


def test_daily_carry_table_pooled_fallback() -> None:
    table = daily_carry_table(_day(), _cfg()).set_index("symbol")
    assert table.loc["GOLDM", "carry_inr_per_g_per_day"] == pytest.approx(1.0)
    assert table.loc["GOLDM", "carry_source"] == "own"
    assert table.loc["GOLDTEN", "carry_source"] == "own"
    # GOLDPETAL has one expiry: borrows the cross-family median (1.0, 1.0) = 1.0
    assert table.loc["GOLDPETAL", "carry_inr_per_g_per_day"] == pytest.approx(1.0)
    assert table.loc["GOLDPETAL", "carry_source"] == "pooled"


def test_daily_carry_table_no_fallback() -> None:
    table = daily_carry_table(_day(), _cfg(pooled_carry_fallback=False)).set_index("symbol")
    assert math.isnan(table.loc["GOLDPETAL", "carry_inr_per_g_per_day"])
    assert table.loc["GOLDPETAL", "carry_source"] == "none"


# ------------------------------------------------------------------ pairing


def test_choose_nearest_dte_pair() -> None:
    day = _day()
    choice = choose_nearest_dte_pair(family_curve(day, "GOLDM"), family_curve(day, "GOLDTEN"), 45)
    assert choice is not None
    assert (choice.expiry_a, choice.expiry_b) == (OCT5, SEP30)  # gap 5 beats 25, 35, 65
    assert (
        choose_nearest_dte_pair(family_curve(day, "GOLDM"), family_curve(day, "GOLDTEN"), 3) is None
    )


def test_align_pair_hand_computed() -> None:
    # GOLDM OCT5 (dte 31, 10040) vs GOLDTEN SEP30 (dte 26, 10050); carry_T = 1.0
    # adj_T = 10050 + 1.0 * (31 - 26) = 10055 ; spread = 10040 - 10055 = -15 ; naive = -10
    rec = align_pair_on_day(_day(), "GOLDM", "GOLDTEN", _cfg())
    assert rec is not None
    assert rec["dte_gap_days"] == 5
    assert rec["carry_b_inr_per_g_per_day"] == pytest.approx(1.0)
    assert rec["adj_price_b_inr_per_g"] == pytest.approx(10055.0)
    assert rec["naive_spread_inr_per_g"] == pytest.approx(-10.0)
    assert rec[SPREAD_COL] == pytest.approx(-15.0)
    assert rec["carry_source"] == "own"


def test_align_pair_reverse_direction() -> None:
    # GOLDTEN SEP30 (26, 10050) vs GOLDM OCT5 (31, 10040); carry_M = 1.0
    # adj_M = 10040 + 1.0 * (26 - 31) = 10035 ; spread = 10050 - 10035 = +15
    rec = align_pair_on_day(_day(), "GOLDTEN", "GOLDM", _cfg())
    assert rec is not None
    assert rec["dte_gap_days"] == -5
    assert rec[SPREAD_COL] == pytest.approx(15.0)


def test_align_pair_steeper_carry_changes_spread() -> None:
    day = _day()
    day.loc[day["expiry_date"] == OCT30, "pure_price_inr_per_g"] = 10110.0
    # carry_T = (10110-10050)/30 = 2.0 ; adj_T = 10050 + 2*5 = 10060 ; spread = -20
    rec = align_pair_on_day(day, "GOLDM", "GOLDTEN", _cfg())
    assert rec is not None
    assert rec["carry_b_inr_per_g_per_day"] == pytest.approx(2.0)
    assert rec[SPREAD_COL] == pytest.approx(-20.0)


def test_align_pair_pooled_carry_for_single_expiry_leg() -> None:
    day = _day()
    day.loc[day["expiry_date"] == OCT30, "pure_price_inr_per_g"] = 10110.0  # carry_T = 2.0
    # pooled median of (1.0, 2.0) = 1.5 ; adj_PETAL = 10052 + 1.5 * 5 = 10059.5
    # spread = 10040 - 10059.5 = -19.5
    rec = align_pair_on_day(day, "GOLDM", "GOLDPETAL", _cfg())
    assert rec is not None
    assert rec["carry_source"] == "pooled"
    assert rec[SPREAD_COL] == pytest.approx(-19.5)


def test_no_carry_means_no_naive_spread() -> None:
    """Acceptance test 7: without a carry estimate the spread is NaN, never the raw gap."""
    rec = align_pair_on_day(_day(), "GOLDM", "GOLDPETAL", _cfg(pooled_carry_fallback=False))
    assert rec is not None
    assert rec["carry_source"] == "none"
    assert math.isnan(rec[SPREAD_COL])
    assert rec["naive_spread_inr_per_g"] == pytest.approx(10040.0 - 10052.0)  # kept for display


def test_no_pair_when_gap_too_large_or_leg_missing() -> None:
    assert align_pair_on_day(_day(), "GOLDM", "GOLDTEN", _cfg(max_dte_gap_days=3)) is None
    assert align_pair_on_day(_day(), "GOLDM", "GOLDGUINEA", _cfg()) is None


# ------------------------------------------------------------------ series


def test_build_pair_series_same_day_only() -> None:
    d2 = date(2026, 9, 7)
    day2 = pd.DataFrame(
        [
            _row("GOLDM", OCT5, 10041.0, d2),
            _row("GOLDM", DEC4, 10101.0, d2),
            _row("GOLDTEN", SEP30, 10049.0, d2),
            _row("GOLDTEN", OCT30, 10079.0, d2),
        ]
    )
    prices = pd.concat([_day(), day2], ignore_index=True)
    series = build_pair_series(prices, "GOLDM", "GOLDTEN", _cfg())
    assert list(series.columns) == PAIR_COLUMNS
    assert series["trade_date"].tolist() == [D, d2]
    assert series[SPREAD_COL].iloc[0] == pytest.approx(-15.0)
    # Day 2: dte OCT5=28, SEP30=23 -> gap 5; carry_T = 30/30 = 1 ; adj = 10054 ; spread = -13
    assert series[SPREAD_COL].iloc[1] == pytest.approx(-13.0)

    # Perturbing a later day never changes an earlier row.
    prices.loc[prices["trade_date"] == d2, "pure_price_inr_per_g"] *= 1.1
    again = build_pair_series(prices, "GOLDM", "GOLDTEN", _cfg())
    assert again.iloc[0].to_dict() == series.iloc[0].to_dict()


def test_build_pair_series_requires_prepared_columns() -> None:
    with pytest.raises(KeyError, match="pure_price_inr_per_g"):
        build_pair_series(_day().drop(columns=["pure_price_inr_per_g"]), "GOLDM", "GOLDTEN", _cfg())


def test_build_pair_series_empty_when_no_days_pair() -> None:
    series = build_pair_series(_day(), "GOLDM", "GOLDGUINEA", _cfg())
    assert series.empty and list(series.columns) == PAIR_COLUMNS


def test_pipeline_with_repo_config() -> None:
    """normalize -> lifecycle -> carry with the real contracts/backtest config."""
    contracts, backtest = load_contracts(), load_backtest()
    raw = pd.DataFrame(
        {
            "trade_date": [D] * 4,
            "symbol": ["GOLDM", "GOLDM", "GOLDTEN", "GOLDTEN"],
            "expiry_date": [OCT5, DEC4, SEP30, OCT30],
            # GOLDM quoted per 10 g at 995: 100000 -> 10040.20 INR/g (PRD appendix A)
            "close_inr": [100_000.0, 100_600.0, 100_500.0, 100_800.0],
        }
    )
    prepared = annotate_lifecycle(
        normalize_prices(raw, contracts), contracts, backtest.exit_buffer_days_before_expiry
    )
    series = build_pair_series(prepared, "GOLDM", "GOLDTEN", backtest.alignment)
    assert len(series) == 1
    rec = series.iloc[0]
    assert rec["price_a_inr_per_g"] == pytest.approx(10000.0 * 999 / 995)
    assert rec["dte_gap_days"] == 5
    # carry_T = (10080 - 10050) / 30 = 1.0 -> adj_T = 10055
    assert rec["adj_price_b_inr_per_g"] == pytest.approx(10055.0)
    assert rec[SPREAD_COL] == pytest.approx(10000.0 * 999 / 995 - 10055.0)


# ------------------------------------------------------------------ constant maturity


def test_constant_maturity_price() -> None:
    curve = family_curve(_day(), "GOLDM")  # (31, 10040), (91, 10100): slope 1.0
    assert constant_maturity_price(curve, 60) == (pytest.approx(10069.0), False)  # 10040 + 29
    assert constant_maturity_price(curve, 31) == (pytest.approx(10040.0), False)
    assert constant_maturity_price(curve, 30) == (pytest.approx(10039.0), True)  # extrapolated
    assert constant_maturity_price(curve, 100) == (pytest.approx(10109.0), True)
    single = family_curve(_day(), "GOLDPETAL")
    assert constant_maturity_price(single, 60) == (pytest.approx(10052.0), True)
    value, flagged = constant_maturity_price(single.iloc[0:0], 60)
    assert math.isnan(value) and flagged


def test_constant_maturity_table() -> None:
    table = constant_maturity_table(_day(), _cfg())
    assert len(table) == 3 * 3  # 3 symbols x 3 grid points
    goldm_60 = table[(table["symbol"] == "GOLDM") & (table["target_dte_days"] == 60)].iloc[0]
    assert goldm_60["cm_price_inr_per_g"] == pytest.approx(10069.0)
    assert not goldm_60["extrapolated"]
    assert goldm_60["n_points"] == 2
