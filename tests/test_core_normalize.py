"""Hand-computed normalization tests (PRD 6.1, Appendix A; acceptance test 1).

Inputs are illustrative arithmetic for the formula, not market data. Every
expected value below is derived by hand in the accompanying comment.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aurumspread.config import ConfigError, ContractsConfig, load_contracts
from aurumspread.core.normalize import (
    PURE_PRICE_COL,
    normalization_factor,
    normalization_table,
    normalize_prices,
    pure_price_inr_per_g,
)


@pytest.fixture(scope="module")
def contracts() -> ContractsConfig:
    return load_contracts()


REL = 1e-12  # float error only, no tolerance beyond that (PRD 2)


def test_goldm_hand_computed(contracts: ContractsConfig) -> None:
    # GOLDM quoted per 10 g, purity 995, basis 999:
    #   100000 / 10 = 10000 INR/g at 995 -> 10000 * 999/995 = 10040.201005... INR/g
    spec = contracts.spec("GOLDM")
    expected = 10000.0 * 999.0 / 995.0
    assert pure_price_inr_per_g(100_000.0, spec, contracts.basis_purity) == pytest.approx(
        expected, rel=REL
    )
    assert round(expected, 2) == 10040.20  # matches PRD Appendix A


def test_goldpetal_hand_computed(contracts: ContractsConfig) -> None:
    # GOLDPETAL quoted per 1 g, purity 999 == basis: 10050 / 1 * 999/999 = 10050 INR/g
    spec = contracts.spec("GOLDPETAL")
    assert pure_price_inr_per_g(10_050.0, spec, contracts.basis_purity) == pytest.approx(
        10_050.0, rel=REL
    )


def test_goldten_hand_computed(contracts: ContractsConfig) -> None:
    # GOLDTEN quoted per 10 g, purity 999: 100500 / 10 * 999/999 = 10050 INR/g
    spec = contracts.spec("GOLDTEN")
    assert pure_price_inr_per_g(100_500.0, spec, contracts.basis_purity) == pytest.approx(
        10_050.0, rel=REL
    )


def test_goldguinea_hand_computed(contracts: ContractsConfig) -> None:
    # GOLDGUINEA quoted per 8 g, purity 999: 80400 / 8 * 999/999 = 10050 INR/g
    spec = contracts.spec("GOLDGUINEA")
    assert pure_price_inr_per_g(80_400.0, spec, contracts.basis_purity) == pytest.approx(
        10_050.0, rel=REL
    )


def test_factors_from_config(contracts: ContractsConfig) -> None:
    b = contracts.basis_purity
    # factor = 1/quote_unit_g * basis/purity
    assert normalization_factor(contracts.spec("GOLDM"), b) == pytest.approx(
        0.1 * 999.0 / 995.0, rel=REL
    )
    assert normalization_factor(contracts.spec("GOLDTEN"), b) == pytest.approx(0.1, rel=REL)
    assert normalization_factor(contracts.spec("GOLDGUINEA"), b) == pytest.approx(0.125, rel=REL)
    assert normalization_factor(contracts.spec("GOLDPETAL"), b) == pytest.approx(1.0, rel=REL)


def test_ratios_are_basis_independent(contracts: ContractsConfig) -> None:
    """PRD 6.1: changing the basis purity rescales every contract by the same constant."""
    goldm, petal = contracts.spec("GOLDM"), contracts.spec("GOLDPETAL")
    ratio_999 = pure_price_inr_per_g(100_000.0, goldm, 999.0) / pure_price_inr_per_g(
        10_050.0, petal, 999.0
    )
    ratio_9999 = pure_price_inr_per_g(100_000.0, goldm, 999.9) / pure_price_inr_per_g(
        10_050.0, petal, 999.9
    )
    assert ratio_999 == pytest.approx(ratio_9999, rel=REL)


def test_normalize_prices_frame(contracts: ContractsConfig) -> None:
    prices = pd.DataFrame(
        {
            "trade_date": [date(2026, 9, 4)] * 4,
            "symbol": ["GOLDM", "GOLDTEN", "GOLDGUINEA", "GOLDPETAL"],
            "expiry_date": [date(2026, 10, 5)] + [date(2026, 9, 30)] * 3,
            "close_inr": [100_000.0, 100_500.0, 80_400.0, 10_050.0],
        }
    )
    out = normalize_prices(prices, contracts)
    assert PURE_PRICE_COL not in prices.columns  # input untouched
    assert out[PURE_PRICE_COL].tolist() == pytest.approx(
        [10000.0 * 999.0 / 995.0, 10_050.0, 10_050.0, 10_050.0], rel=REL
    )
    assert out[PURE_PRICE_COL].dtype == "float64"


def test_normalize_prices_unknown_symbol_raises(contracts: ContractsConfig) -> None:
    prices = pd.DataFrame({"symbol": ["SILVERM"], "close_inr": [1.0]})
    with pytest.raises(ConfigError, match="SILVERM"):
        normalize_prices(prices, contracts)


def test_normalization_table_lists_every_contract(contracts: ContractsConfig) -> None:
    table = normalization_table(contracts).set_index("symbol")
    assert list(table.index) == list(contracts.symbols)
    assert table.loc["GOLDM", "quote_unit_g"] == 10
    assert table.loc["GOLDM", "trading_unit_g"] == 100
    assert table.loc["GOLDM", "factor_to_inr_per_g"] == pytest.approx(0.1 * 999 / 995, rel=REL)
    assert (table["basis_purity"] == 999.0).all()
