"""Tests for core.calendar and core.lifecycle (PRD 6.6; acceptance tests 8 and 9)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aurumspread.config import ContractsConfig, load_contracts
from aurumspread.core.calendar import (
    TradingCalendar,
    add_days_to_expiry,
    days_to_expiry,
    expiry_in_window,
)
from aurumspread.core.lifecycle import (
    annotate_lifecycle,
    contract_status,
    first_buffer_date,
    is_listed,
    last_live_date,
    listing_discrepancies,
    observed_listing_dates,
    tradable_universe,
)

BUFFER = 5  # config/backtest.yaml exit_buffer_days_before_expiry (verify vs tender period)
EXPIRY = date(2026, 10, 5)  # GOLDM-style expiry (3rd-5th)


@pytest.fixture(scope="module")
def contracts() -> ContractsConfig:
    return load_contracts()


# ------------------------------------------------------------------ calendar


def test_days_to_expiry_calendar_days() -> None:
    assert days_to_expiry(date(2026, 9, 30), EXPIRY) == 5
    assert days_to_expiry(EXPIRY, EXPIRY) == 0
    assert days_to_expiry(date(2026, 10, 6), EXPIRY) == -1


def test_add_days_to_expiry_vectorised() -> None:
    frame = pd.DataFrame(
        {"trade_date": [date(2026, 9, 30), date(2026, 10, 5)], "expiry_date": [EXPIRY, EXPIRY]}
    )
    out = add_days_to_expiry(frame)
    assert out["dte_days"].tolist() == [5, 0]
    assert "dte_days" not in frame.columns


def test_expiry_window_check(contracts: ContractsConfig) -> None:
    goldm = contracts.spec("GOLDM")  # 3rd-5th
    assert expiry_in_window(date(2026, 10, 5), goldm)
    assert not expiry_in_window(date(2026, 10, 30), goldm)
    petal = contracts.spec("GOLDPETAL")  # 27th-31st
    assert expiry_in_window(date(2026, 9, 30), petal)
    assert not expiry_in_window(date(2026, 10, 5), petal)


def test_trading_calendar_navigation() -> None:
    # Thu 3, Fri 4, Mon 7, Tue 8 (weekend + a Wednesday holiday on the 9th omitted)
    cal = TradingCalendar(
        [date(2026, 9, 7), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8), date(2026, 9, 3)]
    )
    assert len(cal) == 4
    assert cal.dates == [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]
    assert date(2026, 9, 4) in cal
    assert date(2026, 9, 5) not in cal
    # t+1 fill after Friday is Monday, not Saturday.
    assert cal.next_trading_day(date(2026, 9, 4)) == date(2026, 9, 7)
    assert cal.next_trading_day(date(2026, 9, 5)) == date(2026, 9, 7)  # from a non-trading day
    assert cal.next_trading_day(date(2026, 9, 4), offset=2) == date(2026, 9, 8)
    assert cal.next_trading_day(date(2026, 9, 8)) is None
    assert cal.prev_trading_day(date(2026, 9, 7)) == date(2026, 9, 4)
    assert cal.prev_trading_day(date(2026, 9, 3)) is None
    assert cal.trading_days_between(date(2026, 9, 3), date(2026, 9, 8)) == 3
    assert cal.trading_days_between(date(2026, 9, 8), date(2026, 9, 3)) == -3
    assert cal.window(date(2026, 9, 4), date(2026, 9, 7)) == [date(2026, 9, 4), date(2026, 9, 7)]


def test_trading_calendar_rejects_empty_and_bad_offset() -> None:
    with pytest.raises(ValueError):
        TradingCalendar([])
    with pytest.raises(ValueError):
        TradingCalendar([date(2026, 9, 3)]).next_trading_day(date(2026, 9, 3), offset=0)


# ------------------------------------------------------------------ status


def test_buffer_dates() -> None:
    # expiry Oct 5, buffer 5: buffer covers Sep 30 .. Oct 5; last live day is Sep 29.
    assert first_buffer_date(EXPIRY, BUFFER) == date(2026, 9, 30)
    assert last_live_date(EXPIRY, BUFFER) == date(2026, 9, 29)


@pytest.mark.parametrize(
    ("trade_date", "expected"),
    [
        (date(2026, 9, 29), "live"),  # DTE 6 > buffer
        (date(2026, 9, 30), "exit_buffer"),  # DTE 5 == buffer -> inside buffer
        (date(2026, 10, 5), "exit_buffer"),  # DTE 0
        (date(2026, 10, 6), "expired"),
    ],
)
def test_contract_status_goldm(contracts: ContractsConfig, trade_date: date, expected: str) -> None:
    assert contract_status(trade_date, EXPIRY, contracts.spec("GOLDM"), BUFFER) == expected


def test_goldten_pre_listing(contracts: ContractsConfig) -> None:
    """Acceptance test 9: GOLDTEN never used before its listing date (config 2025-01-01)."""
    spec = contracts.spec("GOLDTEN")
    assert spec.listing_from == date(2025, 1, 1)
    assert not is_listed(date(2024, 12, 31), spec)
    assert is_listed(date(2025, 1, 1), spec)
    assert contract_status(date(2024, 12, 31), date(2025, 1, 31), spec, BUFFER) == "pre_listing"
    assert contract_status(date(2025, 1, 2), date(2025, 1, 31), spec, BUFFER) == "live"
    # Contracts with no listing date are considered listed for the whole history.
    assert is_listed(date(2000, 1, 1), contracts.spec("GOLDM"))


# ------------------------------------------------------------------ frame annotation


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [
                date(2024, 12, 31),
                date(2025, 1, 2),
                date(2026, 9, 29),
                date(2026, 9, 30),
                date(2026, 10, 6),
            ],
            "symbol": ["GOLDTEN", "GOLDTEN", "GOLDM", "GOLDM", "GOLDM"],
            "expiry_date": [date(2025, 1, 31), date(2025, 1, 31), EXPIRY, EXPIRY, EXPIRY],
            "close_inr": [1.0] * 5,
        }
    )


def test_annotate_lifecycle(contracts: ContractsConfig) -> None:
    out = annotate_lifecycle(_frame(), contracts, BUFFER)
    assert out["dte_days"].tolist() == [31, 29, 6, 5, -1]
    assert out["status"].tolist() == ["pre_listing", "live", "live", "exit_buffer", "expired"]
    assert out["tradable"].tolist() == [False, True, True, False, False]


def test_tradable_universe_drops_buffer_and_prelisting(contracts: ContractsConfig) -> None:
    """Acceptance test 8: a contract inside the exit buffer is never in the universe."""
    universe = tradable_universe(_frame(), contracts, BUFFER)
    assert universe["trade_date"].tolist() == [date(2025, 1, 2), date(2026, 9, 29)]
    assert (universe["status"] == "live").all()


def test_annotate_lifecycle_empty(contracts: ContractsConfig) -> None:
    out = annotate_lifecycle(_frame().iloc[0:0], contracts, BUFFER)
    assert list(out.columns)[-3:] == ["dte_days", "status", "tradable"]
    assert out.empty


# ------------------------------------------------------------------ listing verification


def test_listing_discrepancies(contracts: ContractsConfig) -> None:
    prices = pd.DataFrame(
        {
            "symbol": ["GOLDM", "GOLDTEN", "GOLDTEN"],
            "trade_date": [date(2024, 1, 2), date(2025, 3, 3), date(2025, 3, 4)],
        }
    )
    assert observed_listing_dates(prices) == {
        "GOLDM": date(2024, 1, 2),
        "GOLDTEN": date(2025, 3, 3),
    }
    table = listing_discrepancies(prices, contracts).set_index("symbol")
    assert table.loc["GOLDM", "verdict"] == "unchecked"  # no configured listing date
    assert table.loc["GOLDTEN", "verdict"] == "data_starts_after_config"  # 2025-01-01 < 03-03
    assert table.loc["GOLDPETAL", "verdict"] == "unchecked"  # absent from data

    late_cfg = ContractsConfig.model_validate(
        {
            "basis_purity": 999.0,
            "contracts": {
                "GOLDTEN": {
                    **contracts.spec("GOLDTEN").model_dump(mode="json"),
                    "listing_from": "2025-06-01",
                }
            },
        }
    )
    late = listing_discrepancies(prices[prices["symbol"] == "GOLDTEN"], late_cfg)
    assert late.loc[0, "verdict"] == "config_later_than_data"
