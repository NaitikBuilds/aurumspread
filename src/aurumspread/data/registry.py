"""Contract registry: raw CSVs -> validated price history + per-contract table -> parquet.

Contracts are keyed by ``(symbol, expiry_date)`` (AGENTS.md); there is no
continuous series here. ``ingest_raw_dir`` is the single entry point for
"raw -> processed" and is idempotent: re-running on the same raw files yields
identical parquet output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from aurumspread.config import DataSourceConfig
from aurumspread.data.dq import DQLog
from aurumspread.data.parse import parse_bhavcopy_csv
from aurumspread.data.validate import KEY_COLUMNS, check_unique_keys, flag_price_jumps, validate_day

PRICES_FILE = "prices.parquet"
CONTRACTS_FILE = "contracts.parquet"
DQ_FILE = "data_quality_log.parquet"
_RAW_NAME_RE = re.compile(r"^bhavcopy_(\d{4})-(\d{2})-(\d{2})\.csv$")

PRICE_COLUMNS_ORDER = [
    "trade_date",
    "symbol",
    "expiry_date",
    "open_inr",
    "high_inr",
    "low_inr",
    "close_inr",
    "volume",
    "open_interest",
    "thin",
    "jump_flag",
    "source",
    "source_row",
]


def trade_date_from_raw_name(path: Path) -> date:
    """``bhavcopy_2026-09-04.csv`` -> ``date(2026, 9, 4)``; raises on any other name."""
    match = _RAW_NAME_RE.match(path.name)
    if match is None:
        raise ValueError(f"{path.name!r} is not a bhavcopy_YYYY-MM-DD.csv raw file")
    year, month, day = (int(g) for g in match.groups())
    return date(year, month, day)


def build_contract_table(prices: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, expiry_date) with its observed trading window."""
    columns = [
        *KEY_COLUMNS,
        "first_trade_date",
        "last_trade_date",
        "n_trade_days",
        "n_thin_days",
    ]
    if prices.empty:
        return pd.DataFrame(columns=columns)
    grouped = prices.groupby(list(KEY_COLUMNS), sort=True)
    table = grouped.agg(
        first_trade_date=("trade_date", "min"),
        last_trade_date=("trade_date", "max"),
        n_trade_days=("trade_date", "size"),
        n_thin_days=("thin", "sum"),
    ).reset_index()
    table["n_trade_days"] = table["n_trade_days"].astype("int64")
    table["n_thin_days"] = table["n_thin_days"].astype("int64")
    return table[columns]


@dataclass(frozen=True)
class ContractRegistry:
    """Validated daily rows (``prices``) plus the per-contract summary (``contracts``)."""

    prices: pd.DataFrame
    contracts: pd.DataFrame

    @classmethod
    def from_prices(cls, prices: pd.DataFrame) -> ContractRegistry:
        check_unique_keys(prices)
        ordered = prices.sort_values(["trade_date", *KEY_COLUMNS], kind="stable").reset_index(
            drop=True
        )
        return cls(prices=ordered[PRICE_COLUMNS_ORDER], contracts=build_contract_table(ordered))

    def keys(self) -> list[tuple[str, date]]:
        return list(zip(self.contracts["symbol"], self.contracts["expiry_date"], strict=True))

    def history(self, symbol: str, expiry_date: date) -> pd.DataFrame:
        """All rows of one contract, ascending by trade_date."""
        mask = (self.prices["symbol"] == symbol) & (self.prices["expiry_date"] == expiry_date)
        return self.prices[mask].reset_index(drop=True)

    def on_date(self, trade_date: date) -> pd.DataFrame:
        """All contracts that printed a row on ``trade_date``."""
        return self.prices[self.prices["trade_date"] == trade_date].reset_index(drop=True)

    def trade_dates(self) -> list[date]:
        return sorted(self.prices["trade_date"].unique().tolist())

    def save(self, processed_dir: Path, dq_log: DQLog | None = None) -> None:
        processed_dir.mkdir(parents=True, exist_ok=True)
        self.prices.to_parquet(processed_dir / PRICES_FILE, index=False)
        self.contracts.to_parquet(processed_dir / CONTRACTS_FILE, index=False)
        if dq_log is not None:
            dq_log.to_frame().to_parquet(processed_dir / DQ_FILE, index=False)

    @classmethod
    def load(cls, processed_dir: Path) -> ContractRegistry:
        prices = pd.read_parquet(processed_dir / PRICES_FILE)
        contracts = pd.read_parquet(processed_dir / CONTRACTS_FILE)
        return cls(prices=prices, contracts=contracts)


def list_raw_files(raw_dir: Path) -> list[Path]:
    """Accepted raw files in ``raw_dir`` (top level only; ``rejected/`` is ignored)."""
    return sorted(p for p in raw_dir.glob("bhavcopy_*.csv") if _RAW_NAME_RE.match(p.name))


def ingest_raw_dir(
    cfg: DataSourceConfig,
    dq_log: DQLog,
    *,
    raw_dir: Path | None = None,
) -> ContractRegistry:
    """Parse + validate every raw file and build the registry (rules 1-7 of DATA_CONTRACT.md).

    Files are processed in date order; the expected trade date comes from the
    file name, which the fetcher only assigns after the returned Date matched.
    """
    raw_dir = raw_dir if raw_dir is not None else cfg.raw_dir_abs
    files = list_raw_files(raw_dir)
    if not files:
        raise FileNotFoundError(f"no bhavcopy_YYYY-MM-DD.csv files in {raw_dir}")

    days: list[pd.DataFrame] = []
    for path in files:
        expected = trade_date_from_raw_name(path)
        parsed = parse_bhavcopy_csv(path, source_name=path.name)
        days.append(
            validate_day(parsed, cfg.quality.universe, dq_log, expected_trade_date=expected)
        )
    prices = pd.concat(days, ignore_index=True)
    prices = flag_price_jumps(prices, dq_log, max_jump_pct=cfg.quality.max_daily_jump_pct)
    return ContractRegistry.from_prices(prices)
