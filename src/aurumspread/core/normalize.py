"""Normalize quoted prices to INR per gram of pure gold (PRD 6.1, rules/20-finance-domain).

    pure_price_inr_per_g = close_inr / quote_unit_g * (basis_purity / contract_purity)

All constants come from ``config/contracts.yaml``; this is the only module that
may apply the formula. A purity-adjusted price is *not* a fair value: deliverable
purity and delivery terms create a structural spread, so downstream code measures
spreads against their own rolling mean rather than assuming zero.
"""

from __future__ import annotations

import pandas as pd

from aurumspread.config import ConfigError, ContractsConfig, ContractSpec

PURE_PRICE_COL = "pure_price_inr_per_g"


def normalization_factor(spec: ContractSpec, basis_purity: float) -> float:
    """Multiplier turning a quoted price into INR per gram at ``basis_purity``."""
    return (1.0 / spec.quote_unit_g) * (basis_purity / spec.purity)


def pure_price_inr_per_g(close_inr: float, spec: ContractSpec, basis_purity: float) -> float:
    """Scalar form of the normalization formula (used by hand-computed tests)."""
    return close_inr * normalization_factor(spec, basis_purity)


def normalization_table(contracts: ContractsConfig) -> pd.DataFrame:
    """One row per contract with the constants and factor used (dashboard 'normalization table')."""
    rows = []
    for symbol, spec in contracts.contracts.items():
        rows.append(
            {
                "symbol": symbol,
                "trading_unit_g": spec.trading_unit_g,
                "quote_unit_g": spec.quote_unit_g,
                "purity": spec.purity,
                "basis_purity": contracts.basis_purity,
                "factor_to_inr_per_g": normalization_factor(spec, contracts.basis_purity),
            }
        )
    return pd.DataFrame(rows)


def normalize_prices(
    prices: pd.DataFrame,
    contracts: ContractsConfig,
    *,
    price_col: str = "close_inr",
    out_col: str = PURE_PRICE_COL,
) -> pd.DataFrame:
    """Return ``prices`` with an added ``out_col`` (INR/g of pure gold at the basis purity).

    Every symbol in the frame must exist in ``contracts``; otherwise :class:`ConfigError`.
    The input frame is not mutated.
    """
    symbols = set(prices["symbol"].unique().tolist())
    unknown = sorted(symbols - set(contracts.symbols))
    if unknown:
        raise ConfigError(f"symbols without a contract spec: {unknown}")
    factors = {
        symbol: normalization_factor(contracts.spec(symbol), contracts.basis_purity)
        for symbol in symbols
    }
    out = prices.copy()
    out[out_col] = out[price_col].astype("float64") * out["symbol"].map(factors).astype("float64")
    return out
