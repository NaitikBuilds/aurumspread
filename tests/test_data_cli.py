"""Tests for aurumspread.data.cli (no network: fetch is only checked for config gating)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from aurumspread.config import ConfigError
from aurumspread.data.cli import audit_summary, build_parser, main
from aurumspread.data.dq import DQLog
from aurumspread.data.registry import PRICES_FILE, ContractRegistry, ingest_raw_dir
from tests.test_data_registry import _cfg, _three_days


def _write_cfg(tmp_path: Path) -> Path:
    cfg = _cfg(tmp_path)
    path = tmp_path / "data_source.yaml"
    payload = cfg.model_dump(mode="json")
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_parser_parses_dates() -> None:
    args = build_parser().parse_args(["fetch", "--start", "2026-09-01", "--end", "2026-09-04"])
    assert args.command == "fetch"
    assert args.start == date(2026, 9, 1)
    assert args.end == date(2026, 9, 4)


def test_audit_summary_per_symbol(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _three_days(cfg.raw_dir_abs)
    reg = ingest_raw_dir(cfg, DQLog())
    summary = audit_summary(reg).set_index("symbol")
    assert summary.loc["GOLDM", "n_contracts"] == 2
    assert summary.loc["GOLDM", "n_rows"] == 4
    assert summary.loc["GOLDTEN", "first_trade_date"] == date(2026, 9, 2)
    assert summary.loc["GOLDTEN", "last_trade_date"] == date(2026, 9, 3)
    assert summary.loc["GOLDTEN", "thin_share"] == 0.5


def test_ingest_command_writes_parquet_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    cfg_path = _write_cfg(tmp_path)
    _three_days(_cfg(tmp_path).raw_dir_abs)

    assert main(["--config", str(cfg_path), "ingest"]) == 0

    out = capsys.readouterr().out
    assert "== per symbol ==" in out
    assert "GOLDTEN" in out
    assert "price_jump: 1" in out
    assert (tmp_path / "processed" / PRICES_FILE).exists()
    loaded = ContractRegistry.load(tmp_path / "processed")
    assert len(loaded.prices) == 6


def test_fetch_command_refuses_unconfigured_endpoint(tmp_path: Path) -> None:
    cfg_path = _write_cfg(tmp_path)  # url_template is null
    with pytest.raises(ConfigError, match="url_template"):
        main(["--config", str(cfg_path), "fetch", "--start", "2026-09-03", "--end", "2026-09-03"])
