"""Hand-computed tests for core.term_structure (PRD 6.5). Round numbers, not market data."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aurumspread.core.term_structure import (
    CALENDAR_SPREAD_COLUMNS,
    ROLL_DOWN_COLUMNS,
    calendar_spread_on_day,
    calendar_spread_series,
    roll_down_table,
)

D1, D2 = date(2026, 9, 4), date(2026, 9, 7)
OCT5, DEC4, FEB3 = date(2026, 10, 5), date(2026, 12, 4), date(2027, 2, 3)
SEP30 = date(2026, 9, 30)


def _row(symbol: str, expiry: date, price: float, d: date, status: str = "live") -> dict:
    return {
        "trade_date": d,
        "symbol": symbol,
        "expiry_date": expiry,
        "dte_days": (expiry - d).days,
        "status": status,
        "pure_price_inr_per_g": price,
    }


def _prepared() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # D1: GOLDM (31, 10040), (91, 10100), (152, 10190); slope front = 1.0
            _row("GOLDM", OCT5, 10040.0, D1),
            _row("GOLDM", DEC4, 10100.0, D1),
            _row("GOLDM", FEB3, 10190.0, D1),
            _row("GOLDTEN", SEP30, 10050.0, D1),  # single expiry
            # D2 (+3 days): GOLDM (28, 10045), (88, 10102); FEB3 not printed
            _row("GOLDM", OCT5, 10045.0, D2),
            _row("GOLDM", DEC4, 10102.0, D2),
            _row("GOLDM", date(2026, 9, 9), 9000.0, D2, status="exit_buffer"),  # ignored
        ]
    )


# ------------------------------------------------------------------ calendar spread


def test_calendar_spread_hand_computed() -> None:
    day = _prepared()[_prepared()["trade_date"] == D1]
    rec = calendar_spread_on_day(day, "GOLDM")
    assert rec is not None
    assert (rec["expiry_near"], rec["expiry_far"]) == (OCT5, DEC4)  # front two, FEB3 unused
    assert rec["dte_gap_days"] == 60
    assert rec["calendar_spread_inr_per_g"] == pytest.approx(60.0)  # 10100 - 10040
    assert rec["carry_inr_per_g_per_day"] == pytest.approx(1.0)  # 60 / 60
    # roll yield = 1.0 * 365 / 10040 * 100 = 3.6354...%
    assert rec["roll_yield_pct_annualised"] == pytest.approx(365.0 / 10040.0 * 100.0)


def test_calendar_spread_needs_two_live_expiries() -> None:
    day = _prepared()[_prepared()["trade_date"] == D1]
    assert calendar_spread_on_day(day, "GOLDTEN") is None
    assert calendar_spread_on_day(day, "GOLDPETAL") is None


def test_calendar_spread_series() -> None:
    series = calendar_spread_series(_prepared(), "GOLDM")
    assert list(series.columns) == CALENDAR_SPREAD_COLUMNS
    assert series["trade_date"].tolist() == [D1, D2]
    # D2: (28, 10045) vs (88, 10102): spread 57 over 60 days = 0.95/day
    assert series.loc[1, "carry_inr_per_g_per_day"] == pytest.approx(57.0 / 60.0)
    assert calendar_spread_series(_prepared(), "GOLDTEN").empty


# ------------------------------------------------------------------ roll-down


def test_roll_down_hand_computed() -> None:
    table = roll_down_table(_prepared(), "GOLDM").set_index("expiry_date")
    assert list(table.columns) == [c for c in ROLL_DOWN_COLUMNS if c != "expiry_date"]
    assert set(table.index) == {OCT5, DEC4}  # FEB3 absent on D2 -> skipped

    # OCT5: yesterday's curve at today's DTE 28 is left of the front point (31) ->
    # extrapolate slope 1.0: 10040 + 1.0 * (28 - 31) = 10037
    # roll_down = 10037 - 10040 = -3 ; curve_change = 10045 - 10037 = +8 ; total = +5
    oct5 = table.loc[OCT5]
    assert (oct5["dte_prev"], oct5["dte_now"]) == (31, 28)
    assert oct5["cm_prev_at_dte_now_inr_per_g"] == pytest.approx(10037.0)
    assert oct5["roll_down_inr_per_g"] == pytest.approx(-3.0)
    assert oct5["curve_change_inr_per_g"] == pytest.approx(8.0)
    assert oct5["total_change_inr_per_g"] == pytest.approx(5.0)
    assert bool(oct5["extrapolated"]) is True

    # DEC4: DTE 91 -> 88 lies inside [31, 91]; interpolate: 10040 + 1.0 * (88 - 31) = 10097
    # roll_down = 10097 - 10100 = -3 ; curve_change = 10102 - 10097 = +5 ; total = +2
    dec4 = table.loc[DEC4]
    assert dec4["cm_prev_at_dte_now_inr_per_g"] == pytest.approx(10097.0)
    assert dec4["roll_down_inr_per_g"] == pytest.approx(-3.0)
    assert dec4["curve_change_inr_per_g"] == pytest.approx(5.0)
    assert dec4["total_change_inr_per_g"] == pytest.approx(2.0)
    assert bool(dec4["extrapolated"]) is False


def test_roll_down_identity_holds() -> None:
    table = roll_down_table(_prepared(), "GOLDM")
    total = table["roll_down_inr_per_g"] + table["curve_change_inr_per_g"]
    assert total.tolist() == pytest.approx(table["total_change_inr_per_g"].tolist(), abs=1e-12)


def test_roll_down_flat_curve_is_zero() -> None:
    """With an unchanged, flat curve the whole move is zero and roll-down is zero."""
    frame = pd.DataFrame(
        [
            _row("GOLDM", OCT5, 10000.0, D1),
            _row("GOLDM", DEC4, 10000.0, D1),
            _row("GOLDM", OCT5, 10000.0, D2),
            _row("GOLDM", DEC4, 10000.0, D2),
        ]
    )
    table = roll_down_table(frame, "GOLDM")
    assert table["roll_down_inr_per_g"].tolist() == pytest.approx([0.0, 0.0])
    assert table["curve_change_inr_per_g"].tolist() == pytest.approx([0.0, 0.0])


def test_roll_down_empty_cases() -> None:
    assert roll_down_table(_prepared(), "GOLDTEN").empty  # only one day
    assert roll_down_table(_prepared(), "GOLDPETAL").empty  # absent
    assert list(roll_down_table(_prepared(), "GOLDPETAL").columns) == ROLL_DOWN_COLUMNS
