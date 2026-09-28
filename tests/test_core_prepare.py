"""End-to-end: raw CSV dir -> registry -> prepared frame -> pair spreads (repo config)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aurumspread.config import AppConfig, load_all
from aurumspread.core.carry import PAIR_COLUMNS, SPREAD_COL
from aurumspread.core.prepare import all_pair_spreads, pair_spreads, prepare_prices
from aurumspread.data.dq import DQLog
from aurumspread.data.registry import ingest_raw_dir

HEADER = "Symbol,Date,ExpiryDate,Open,High,Low,Close,Volume,OpenInterest"
D = date(2026, 9, 4)


def _write_day(raw_dir: Path) -> None:
    """Round-number quotes so INR/g values are checkable by hand (not market data)."""
    raw_dir.mkdir(parents=True)
    rows = [
        # GOLDM per 10 g @995: 100000 -> 10040.20 ; 100600 -> 10100.44 (DTE 31, 91)
        "GOLDM,09/04/2026,05OCT2026,100000,100100,99900,100000,100,500",
        "GOLDM,09/04/2026,04DEC2026,100600,100700,100500,100600,50,300",
        # GOLDTEN per 10 g @999: 100500 -> 10050 ; 100800 -> 10080 (DTE 26, 56)
        "GOLDTEN,09/04/2026,30SEP2026,100500,100600,100400,100500,100,500",
        "GOLDTEN,09/04/2026,30OCT2026,100800,100900,100700,100800,60,200",
        # GOLDPETAL per 1 g @999: 10052 (single expiry) ; GOLDGUINEA inside exit buffer
        "GOLDPETAL,09/04/2026,30SEP2026,10052,10053,10051,10052,10,20",
        "GOLDGUINEA,09/04/2026,05SEP2026,80400,80401,80399,80400,1,1",
    ]
    (raw_dir / "bhavcopy_2026-09-04.csv").write_text("\n".join([HEADER, *rows]) + "\n")


@pytest.fixture()
def cfg(tmp_path: Path) -> AppConfig:
    base = load_all()
    ds = base.data_source.model_copy(
        update={"raw_dir": tmp_path / "raw", "processed_dir": tmp_path / "processed"}
    )
    return base.model_copy(update={"data_source": ds})


def test_prepare_and_pair_spreads_end_to_end(cfg: AppConfig) -> None:
    _write_day(cfg.data_source.raw_dir_abs)
    registry = ingest_raw_dir(cfg.data_source, DQLog())

    prepared = prepare_prices(registry, cfg)
    assert {"pure_price_inr_per_g", "dte_days", "status", "tradable"} <= set(prepared.columns)
    by_key = prepared.set_index(["symbol", "expiry_date"])
    assert by_key.loc[("GOLDM", date(2026, 10, 5)), "pure_price_inr_per_g"] == pytest.approx(
        10000.0 * 999 / 995
    )
    assert by_key.loc[("GOLDGUINEA", date(2026, 9, 5)), "status"] == "exit_buffer"  # DTE 1

    series = pair_spreads(prepared, "GOLDM", "GOLDTEN", cfg)
    assert list(series.columns) == PAIR_COLUMNS
    assert len(series) == 1
    # carry_T = (10080-10050)/(56-26) = 1.0 ; adj_T = 10050 + 1.0*(31-26) = 10055
    assert series.loc[0, SPREAD_COL] == pytest.approx(10000.0 * 999 / 995 - 10055.0)

    stacked = all_pair_spreads(prepared, cfg)
    pairs = set(zip(stacked["symbol_a"], stacked["symbol_b"], strict=True))
    # GOLDGUINEA is in the buffer so no pair involves it; the other 3 families give 6 ordered pairs.
    assert pairs == {
        (a, b)
        for a in ("GOLDM", "GOLDTEN", "GOLDPETAL")
        for b in ("GOLDM", "GOLDTEN", "GOLDPETAL")
        if a != b
    }
    petal_leg_b = stacked[(stacked["symbol_a"] == "GOLDM") & (stacked["symbol_b"] == "GOLDPETAL")]
    assert petal_leg_b["carry_source"].iloc[0] == "pooled"  # single-expiry family
