"""Command line for the data layer.

    python -m aurumspread.data.cli fetch --start 2025-01-01 --end 2026-09-26
    python -m aurumspread.data.cli ingest

``fetch`` needs ``config/data_source.yaml`` filled in (endpoint verified by a human).
``ingest`` parses whatever is in ``data/raw``, writes parquet to ``data/processed``
and prints the audit summary needed to freeze dates in DECISIONS.md.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date

import pandas as pd

from aurumspread.config import load_data_source
from aurumspread.data.dq import DQLog
from aurumspread.data.fetch import fetch_range
from aurumspread.data.registry import ContractRegistry, ingest_raw_dir


def audit_summary(registry: ContractRegistry) -> pd.DataFrame:
    """Per-symbol audit: contracts seen, rows, first/last trade date, thin share."""
    prices = registry.prices
    if prices.empty:
        return pd.DataFrame(
            columns=["symbol", "n_contracts", "n_rows", "first_trade_date", "last_trade_date"]
        )
    per_symbol = prices.groupby("symbol", sort=True).agg(
        n_contracts=("expiry_date", "nunique"),
        n_rows=("trade_date", "size"),
        first_trade_date=("trade_date", "min"),
        last_trade_date=("trade_date", "max"),
        thin_share=("thin", "mean"),
    )
    return per_symbol.reset_index()


def _cmd_fetch(args: argparse.Namespace) -> int:
    cfg = load_data_source(args.config)
    log = DQLog()
    results = fetch_range(args.start, args.end, cfg, dq_log=log)
    counts = Counter(r.status for r in results)
    print(f"fetched={counts['fetched']} cached={counts['cached']} discarded={counts['discarded']}")
    for event in log.events:
        print(f"  {event.trade_date} {event.code}: {event.message}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    cfg = load_data_source(args.config)
    log = DQLog()
    registry = ingest_raw_dir(cfg, log)
    registry.save(cfg.processed_dir_abs, log)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print("== per symbol ==")
        print(audit_summary(registry).to_string(index=False))
        print("\n== per contract ==")
        print(registry.contracts.to_string(index=False))
    print("\n== data quality events ==")
    for code, n in sorted(Counter(log.codes()).items()):
        print(f"  {code}: {n}")
    print(f"\nwrote parquet to {cfg.processed_dir_abs}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aurumspread.data")
    parser.add_argument("--config", default=None, help="path to data_source.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download raw Bhavcopy files for a date range")
    fetch.add_argument("--start", type=date.fromisoformat, required=True)
    fetch.add_argument("--end", type=date.fromisoformat, required=True)
    fetch.set_defaults(func=_cmd_fetch)

    ingest = sub.add_parser("ingest", help="raw -> parquet, print audit summary")
    ingest.set_defaults(func=_cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
