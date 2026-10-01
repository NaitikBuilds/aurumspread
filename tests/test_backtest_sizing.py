"""T08 lot sizing. lot_size_units=1 in these fixtures is not an MCX fact."""

from __future__ import annotations

from aurumspread.backtest.sizing import size_pair
from aurumspread.config import load_contracts


def _with_lots(units: dict[str, int | None]):
    contracts = load_contracts()
    updated = {
        symbol: contracts.spec(symbol).model_copy(update={"lot_size_units": lot_size})
        for symbol, lot_size in units.items()
    }
    return contracts.model_copy(update={"contracts": {**contracts.contracts, **updated}})


def test_repo_lots_are_not_assumed() -> None:
    sized = size_pair("GOLDM", "GOLDTEN", 200.0, load_contracts())
    assert sized.sized is False
    assert sized.lots_a is None
    assert sized.residual_g is None
    assert sized.missing == ("lot_size_units:GOLDM", "lot_size_units:GOLDTEN")


def test_goldm_goldten_match_with_no_residual() -> None:
    # trading units 100 g and 10 g, one unit per lot. Target 250 g -> 2 and 20 lots.
    contracts = _with_lots({"GOLDM": 1, "GOLDTEN": 1})
    sized = size_pair("GOLDM", "GOLDTEN", 250.0, contracts)
    assert sized.lots_a == 2
    assert sized.lots_b == 20
    assert sized.qty_g_a == 200.0
    assert sized.qty_g_b == -200.0
    assert sized.residual_g == 0.0


def test_guinea_leaves_a_four_gram_residual() -> None:
    # 100 g GOLDM vs 8 g GOLDGUINEA: 12 guinea lots = 96 g, residual +4 g.
    contracts = _with_lots({"GOLDM": 1, "GOLDGUINEA": 1})
    sized = size_pair("GOLDM", "GOLDGUINEA", 100.0, contracts)
    assert sized.lots_a == 1
    assert sized.lots_b == 12
    assert sized.qty_g_a == 100.0
    assert sized.qty_g_b == -96.0
    assert sized.residual_g == 4.0


def test_short_spread_flips_signs() -> None:
    contracts = _with_lots({"GOLDM": 1, "GOLDGUINEA": 1})
    sized = size_pair("GOLDM", "GOLDGUINEA", 100.0, contracts, side="short_spread")
    assert sized.qty_g_a == -100.0
    assert sized.qty_g_b == 96.0
    assert sized.residual_g == -4.0
