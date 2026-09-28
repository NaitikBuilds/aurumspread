"""Contract lifecycle: listing, live window, exit buffer, expiry (PRD 6.6).

Status of a contract on a trade date, in order of precedence:

* ``pre_listing`` - trade_date < ``listing_from`` (GOLDTEN before 2025); never usable
* ``expired``     - trade_date > expiry_date
* ``exit_buffer`` - 0 <= days_to_expiry <= exit_buffer_days; no entries, forced exit
* ``live``        - trade_date < expiry - exit_buffer (BACKTEST_PROTOCOL rule 4)

The exit buffer is counted in calendar days, matching ``days_to_expiry``.
TODO(verify): confirm buffer vs MCX tender period once ``tender_period`` is filled.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from aurumspread.config import ContractsConfig, ContractSpec
from aurumspread.core.calendar import DTE_COL, add_days_to_expiry, days_to_expiry

Status = Literal["pre_listing", "live", "exit_buffer", "expired"]
STATUS_COL = "status"
TRADABLE_COL = "tradable"


def is_listed(trade_date: date, spec: ContractSpec) -> bool:
    return spec.listing_from is None or trade_date >= spec.listing_from


def first_buffer_date(expiry_date: date, exit_buffer_days: int) -> date:
    """First date inside the exit buffer (= forced-exit fill date at the latest)."""
    return expiry_date - timedelta(days=exit_buffer_days)


def last_live_date(expiry_date: date, exit_buffer_days: int) -> date:
    """Last date on which a signal may be generated for this contract."""
    return first_buffer_date(expiry_date, exit_buffer_days) - timedelta(days=1)


def contract_status(
    trade_date: date,
    expiry_date: date,
    spec: ContractSpec,
    exit_buffer_days: int,
) -> Status:
    if not is_listed(trade_date, spec):
        return "pre_listing"
    dte = days_to_expiry(trade_date, expiry_date)
    if dte < 0:
        return "expired"
    if dte <= exit_buffer_days:
        return "exit_buffer"
    return "live"


def annotate_lifecycle(
    prices: pd.DataFrame,
    contracts: ContractsConfig,
    exit_buffer_days: int,
) -> pd.DataFrame:
    """Add ``dte_days``, ``status`` and ``tradable`` columns (vectorised per symbol).

    ``tradable`` is purely calendar-based (status == live); liquidity filters are
    applied later by the backtest layer.
    """
    out = add_days_to_expiry(prices)
    trade = pd.to_datetime(out["trade_date"])
    listing_from = out["symbol"].map(
        {s: contracts.spec(s).listing_from for s in out["symbol"].unique()}
    )
    pre_listing = listing_from.notna() & (trade < pd.to_datetime(listing_from))
    dte = out[DTE_COL]
    status = np.select(
        [pre_listing.to_numpy(), (dte < 0).to_numpy(), (dte <= exit_buffer_days).to_numpy()],
        ["pre_listing", "expired", "exit_buffer"],
        default="live",
    )
    out[STATUS_COL] = status
    out[TRADABLE_COL] = out[STATUS_COL] == "live"
    return out


def tradable_universe(
    prices_on_day: pd.DataFrame,
    contracts: ContractsConfig,
    exit_buffer_days: int,
) -> pd.DataFrame:
    """Rows of one day's frame that are live (calendar-wise)."""
    annotated = annotate_lifecycle(prices_on_day, contracts, exit_buffer_days)
    return annotated[annotated[TRADABLE_COL]].reset_index(drop=True)


def observed_listing_dates(prices: pd.DataFrame) -> dict[str, date]:
    """First trade date per symbol actually seen in the data."""
    if prices.empty:
        return {}
    first = prices.groupby("symbol", sort=True)["trade_date"].min()
    return {str(k): v for k, v in first.items()}


def listing_discrepancies(prices: pd.DataFrame, contracts: ContractsConfig) -> pd.DataFrame:
    """Compare configured ``listing_from`` with the first observed trade date.

    Used to verify the ``listing_from`` field (config/contracts.yaml marks it
    ``verify:true``). A configured date *later* than the first observed row means
    real data would be thrown away; *earlier* just means the history starts late.
    """
    observed = observed_listing_dates(prices)
    rows = []
    for symbol in contracts.symbols:
        configured = contracts.spec(symbol).listing_from
        seen = observed.get(symbol)
        if configured is None or seen is None:
            verdict = "unchecked"
        elif configured > seen:
            verdict = "config_later_than_data"
        elif configured < seen:
            verdict = "data_starts_after_config"
        else:
            verdict = "match"
        rows.append(
            {
                "symbol": symbol,
                "configured_listing_from": configured,
                "first_observed_trade_date": seen,
                "verdict": verdict,
            }
        )
    return pd.DataFrame(rows)
