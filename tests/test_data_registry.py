"""Tests for aurumspread.data.registry (T03: raw dir -> registry -> parquet).

Raw files here follow DATA_CONTRACT.md formats and exercise the pipeline
mechanics only; they are not market data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from aurumspread.config import DataSourceConfig
from aurumspread.data.dq import DQLog
from aurumspread.data.registry import (
    CONTRACTS_FILE,
    DQ_FILE,
    PRICE_COLUMNS_ORDER,
    PRICES_FILE,
    ContractRegistry,
    build_contract_table,
    ingest_raw_dir,
    list_raw_files,
    trade_date_from_raw_name,
)
from aurumspread.data.validate import DuplicateKeyError

HEADER = "Symbol,Date,ExpiryDate,Open,High,Low,Close,Volume,OpenInterest"
D1, D2, D3 = date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)


def _cfg(tmp_path: Path) -> DataSourceConfig:
    return DataSourceConfig.model_validate(
        {
            "url_template": None,
            "method": "GET",
            "params_template": {},
            "headers": {},
            "request_date_format": "%d/%m/%Y",
            "timeout_s": 5,
            "min_interval_s": 0,
            "max_retries": 0,
            "backoff_base_s": 1,
            "raw_dir": str(tmp_path / "raw"),
            "processed_dir": str(tmp_path / "processed"),
            "quality": {"max_daily_jump_pct": 5.0, "universe": ["GOLDM", "GOLDTEN"]},
        }
    )


def _write_raw(raw_dir: Path, trade_date: date, rows: list[str]) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"bhavcopy_{trade_date:%Y-%m-%d}.csv"
    lines = [HEADER] + [f"{r[0]},{trade_date:%m/%d/%Y},{r[1]},{r[2]}" for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _three_days(raw_dir: Path) -> None:
    # symbol, expiry, "Open,High,Low,Close,Volume,OI"
    _write_raw(
        raw_dir,
        D1,
        [
            ("GOLDM   ", "05OCT2026", "100,101,99,100,10,50"),
            ("GOLDTEN", "30SEP2026", "10,11,9,10,5,20"),
            ("SILVERM", "04DEC2026", "1,1,1,1,1,1"),  # outside universe
        ],
    )
    _write_raw(
        raw_dir,
        D2,
        [
            ("GOLDM", "05OCT2026", "100,107,99,106,10,50"),  # +6% jump vs D1
            ("GOLDTEN", "30SEP2026", "10,11,9,10,0,20"),  # zero volume -> thin
        ],
    )
    _write_raw(
        raw_dir,
        D3,
        [
            ("GOLDM", "05OCT2026", "106,107,105,106,10,50"),
            ("GOLDM", "04DEC2026", "110,111,109,110,2,5"),  # new expiry appears
        ],
    )


# ------------------------------------------------------------------ helpers


def test_trade_date_from_raw_name() -> None:
    assert trade_date_from_raw_name(Path("bhavcopy_2026-09-04.csv")) == D3
    with pytest.raises(ValueError, match="not a bhavcopy"):
        trade_date_from_raw_name(Path("bhavcopy_req-2026-09-04_got-2026-09-03.csv"))


def test_list_raw_files_ignores_rejected_dir(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    _write_raw(raw_dir, D1, [("GOLDM", "05OCT2026", "1,1,1,1,1,1")])
    (raw_dir / "rejected").mkdir()
    (raw_dir / "rejected" / "bhavcopy_req-2026-09-03_got-2026-09-02.csv").write_text(HEADER)
    (raw_dir / "notes.txt").write_text("x")
    assert [p.name for p in list_raw_files(raw_dir)] == ["bhavcopy_2026-09-02.csv"]


def test_build_contract_table_empty() -> None:
    table = build_contract_table(pd.DataFrame(columns=PRICE_COLUMNS_ORDER))
    assert list(table.columns) == [
        "symbol",
        "expiry_date",
        "first_trade_date",
        "last_trade_date",
        "n_trade_days",
        "n_thin_days",
    ]
    assert table.empty


# ------------------------------------------------------------------ ingest end to end


def test_ingest_builds_registry_and_dq_log(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _three_days(cfg.raw_dir_abs)
    log = DQLog()

    reg = ingest_raw_dir(cfg, log)

    assert list(reg.prices.columns) == PRICE_COLUMNS_ORDER
    assert reg.trade_dates() == [D1, D2, D3]
    assert reg.keys() == [
        ("GOLDM", date(2026, 10, 5)),
        ("GOLDM", date(2026, 12, 4)),
        ("GOLDTEN", date(2026, 9, 30)),
    ]
    # SILVERM ignored, everything else kept: 2 + 2 + 2 rows.
    assert len(reg.prices) == 6
    assert "SILVERM" not in set(reg.prices["symbol"])

    goldm_oct = reg.history("GOLDM", date(2026, 10, 5))
    assert goldm_oct["trade_date"].tolist() == [D1, D2, D3]
    assert goldm_oct["close_inr"].tolist() == [100.0, 106.0, 106.0]
    assert goldm_oct["jump_flag"].tolist() == [False, True, False]
    assert goldm_oct["source"].tolist() == [
        "bhavcopy_2026-09-02.csv",
        "bhavcopy_2026-09-03.csv",
        "bhavcopy_2026-09-04.csv",
    ]
    assert goldm_oct["source_row"].tolist() == [2, 2, 2]

    ten = reg.history("GOLDTEN", date(2026, 9, 30))
    assert ten["thin"].tolist() == [False, True]

    contracts = reg.contracts.set_index(["symbol", "expiry_date"])
    assert contracts.loc[("GOLDM", date(2026, 10, 5)), "n_trade_days"] == 3
    assert contracts.loc[("GOLDM", date(2026, 12, 4)), "first_trade_date"] == D3
    assert contracts.loc[("GOLDTEN", date(2026, 9, 30)), "n_thin_days"] == 1
    assert contracts.loc[("GOLDTEN", date(2026, 9, 30)), "last_trade_date"] == D2

    assert sorted(log.codes()) == ["price_jump", "symbols_ignored", "thin"]
    assert len(reg.on_date(D3)) == 2


def test_ingest_is_deterministic(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _three_days(cfg.raw_dir_abs)
    a = ingest_raw_dir(cfg, DQLog())
    b = ingest_raw_dir(cfg, DQLog())
    pd.testing.assert_frame_equal(a.prices, b.prices)
    pd.testing.assert_frame_equal(a.contracts, b.contracts)


def test_ingest_raises_on_duplicate_key_in_raw_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_raw(
        cfg.raw_dir_abs,
        D1,
        [("GOLDM", "05OCT2026", "1,1,1,1,1,1"), ("GOLDM", "05OCT2026", "1,1,1,1,1,1")],
    )
    with pytest.raises(DuplicateKeyError):
        ingest_raw_dir(cfg, DQLog())


def test_ingest_requires_raw_files(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw_dir_abs.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="no bhavcopy"):
        ingest_raw_dir(cfg, DQLog())


# ------------------------------------------------------------------ parquet round trip


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _three_days(cfg.raw_dir_abs)
    log = DQLog()
    reg = ingest_raw_dir(cfg, log)

    reg.save(cfg.processed_dir_abs, log)
    assert {p.name for p in cfg.processed_dir_abs.iterdir()} == {
        PRICES_FILE,
        CONTRACTS_FILE,
        DQ_FILE,
    }

    loaded = ContractRegistry.load(cfg.processed_dir_abs)
    pd.testing.assert_frame_equal(loaded.prices, reg.prices)
    pd.testing.assert_frame_equal(loaded.contracts, reg.contracts)
    # Dates survive as python dates so (symbol, expiry_date) lookups keep working.
    assert loaded.history("GOLDM", date(2026, 10, 5))["trade_date"].tolist() == [D1, D2, D3]
    dq = pd.read_parquet(cfg.processed_dir_abs / DQ_FILE)
    assert sorted(dq["code"]) == ["price_jump", "symbols_ignored", "thin"]
