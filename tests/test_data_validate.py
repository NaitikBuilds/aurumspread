"""Tests for aurumspread.data.validate (DATA_CONTRACT.md validation rules 1-7).

Frames are built directly with typed columns (as parse_bhavcopy_csv would
produce). Values exercise the rules, they are not market data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from aurumspread.data.dq import DQLog
from aurumspread.data.validate import (
    DuplicateKeyError,
    check_unique_keys,
    flag_price_jumps,
    validate_day,
)

D1 = date(2026, 9, 3)
D2 = date(2026, 9, 4)
UNIVERSE = ("GOLDM", "GOLDTEN", "GOLDGUINEA", "GOLDPETAL")


def _row(
    symbol: str = "GOLDM",
    trade_date: date = D2,
    expiry: date = date(2026, 10, 5),
    o: float = 100.0,
    h: float = 101.0,
    lo: float = 99.0,
    c: float = 100.5,
    vol: float | None = 10.0,
    oi: float | None = 20.0,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "expiry_date": expiry,
        "open_inr": o,
        "high_inr": h,
        "low_inr": lo,
        "close_inr": c,
        "volume": np.nan if vol is None else vol,
        "open_interest": np.nan if oi is None else oi,
        "source": "test",
    }


def _frame(*rows: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ------------------------------------------------------------------ happy path


def test_clean_day_passes_untouched_and_sorted() -> None:
    log = DQLog()
    frame = _frame(_row("GOLDTEN", expiry=date(2026, 9, 30)), _row("GOLDM"))
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    assert out["symbol"].tolist() == ["GOLDM", "GOLDTEN"]
    assert out["thin"].tolist() == [False, False]
    assert len(log) == 0


# ------------------------------------------------------------------ rule 1 (defensive)


def test_wrong_trade_date_rows_dropped() -> None:
    log = DQLog()
    frame = _frame(_row(trade_date=D1), _row("GOLDTEN", expiry=date(2026, 9, 30)))
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    assert out["symbol"].tolist() == ["GOLDTEN"]
    assert log.codes() == ["date_mismatch"]


# ------------------------------------------------------------------ rule 2


def test_symbols_outside_universe_ignored_not_errored() -> None:
    log = DQLog()
    frame = _frame(_row("SILVERM"), _row("GOLDM"), _row("GOLD", expiry=date(2026, 10, 5)))
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    assert out["symbol"].tolist() == ["GOLDM"]
    assert log.codes() == ["symbols_ignored"]
    assert "GOLD" in log.events[0].message and "SILVERM" in log.events[0].message


# ------------------------------------------------------------------ rule 3


@pytest.mark.parametrize(
    "prices",
    [
        {"c": 0.0, "lo": 0.0},  # close must be > 0
        {"c": -5.0, "lo": -10.0},
        {"h": 98.0, "lo": 99.0},  # high < low
        {"c": 102.0},  # close above high (101)
        {"c": 98.0},  # close below low (99)
    ],
)
def test_bad_prices_dropped_and_logged(prices: dict[str, float]) -> None:
    log = DQLog()
    frame = _frame(_row(**prices), _row("GOLDTEN", expiry=date(2026, 9, 30)))
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    assert out["symbol"].tolist() == ["GOLDTEN"]
    assert log.codes() == ["bad_price"]
    assert log.events[0].key == "GOLDM/2026-10-05"


def test_close_equal_to_high_or_low_is_valid() -> None:
    log = DQLog()
    frame = _frame(_row(c=101.0), _row("GOLDTEN", expiry=date(2026, 9, 30), c=99.0))
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    assert len(out) == 2
    assert len(log) == 0


# ------------------------------------------------------------------ rule 4


@pytest.mark.parametrize(
    ("vol", "oi"),
    [(None, 20.0), (0.0, 20.0), (10.0, None), (10.0, 0.0), (None, None)],
)
def test_missing_or_zero_volume_oi_flagged_thin_but_kept(
    vol: float | None, oi: float | None
) -> None:
    log = DQLog()
    out = validate_day(_frame(_row(vol=vol, oi=oi)), UNIVERSE, log, expected_trade_date=D2)
    assert len(out) == 1
    assert bool(out.loc[0, "thin"]) is True
    assert log.codes() == ["thin"]


# ------------------------------------------------------------------ rule 6


def test_duplicate_keys_raise() -> None:
    frame = _frame(_row(), _row(c=100.4))
    with pytest.raises(DuplicateKeyError, match="GOLDM"):
        validate_day(frame, UNIVERSE, DQLog(), expected_trade_date=D2)


def test_same_symbol_different_expiry_is_not_duplicate() -> None:
    frame = _frame(_row(expiry=date(2026, 10, 5)), _row(expiry=date(2026, 12, 4)))
    check_unique_keys(frame)  # no raise
    out = validate_day(frame, UNIVERSE, DQLog(), expected_trade_date=D2)
    assert len(out) == 2


def test_same_key_on_different_days_is_not_duplicate() -> None:
    check_unique_keys(_frame(_row(trade_date=D1), _row(trade_date=D2)))


# ------------------------------------------------------------------ rule 7


def test_expired_contract_dropped() -> None:
    log = DQLog()
    frame = _frame(_row(expiry=date(2026, 9, 3)), _row(expiry=date(2026, 9, 4)))  # D2 = Sep 4
    out = validate_day(frame, UNIVERSE, log, expected_trade_date=D2)
    # Expiry == trade date is allowed (last trading day); earlier is not.
    assert out["expiry_date"].tolist() == [date(2026, 9, 4)]
    assert log.codes() == ["expired_contract"]


# ------------------------------------------------------------------ rule 5


def test_price_jump_flagged_not_dropped() -> None:
    log = DQLog()
    frame = _frame(
        _row(trade_date=D1, c=100.0, h=101.0, lo=99.0),
        _row(trade_date=D2, c=106.0, h=107.0, lo=99.0),  # +6% > 5% limit
        _row("GOLDTEN", trade_date=D1, expiry=date(2026, 9, 30), c=100.0),
        _row("GOLDTEN", trade_date=D2, expiry=date(2026, 9, 30), c=104.0, h=105.0),  # +4%
    )
    out = flag_price_jumps(frame, log, max_jump_pct=5.0)
    assert len(out) == 4
    assert out["jump_flag"].tolist() == [False, True, False, False]
    assert log.codes() == ["price_jump"]
    assert log.events[0].key == "GOLDM/2026-10-05"
    assert "+6.00%" in log.events[0].message


def test_price_jump_not_computed_across_contracts() -> None:
    """Two expiries of the same symbol with very different prices must not flag each other."""
    log = DQLog()
    frame = _frame(
        _row(trade_date=D1, expiry=date(2026, 10, 5), c=100.0),
        _row(trade_date=D1, expiry=date(2026, 12, 4), c=150.0, h=151.0, lo=149.0),
    )
    out = flag_price_jumps(frame, log, max_jump_pct=5.0)
    assert out["jump_flag"].tolist() == [False, False]
    assert len(log) == 0


def test_price_jump_on_empty_frame() -> None:
    out = flag_price_jumps(_frame().reindex(columns=list(_row().keys())), DQLog(), max_jump_pct=5)
    assert "jump_flag" in out.columns
    assert len(out) == 0
