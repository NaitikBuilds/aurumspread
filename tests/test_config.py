"""Tests for aurumspread.config (T01: typed config loaders)."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from aurumspread.config import (
    DEFAULT_CONFIG_DIR,
    AppConfig,
    BacktestConfig,
    ConfigError,
    ContractsConfig,
    CostsConfig,
    load_all,
    load_backtest,
    load_contracts,
    load_costs,
)

# A minimal valid contracts.yaml payload. Values mirror the PS-03 table for two families.
VALID_CONTRACTS: dict[str, Any] = {
    "basis_purity": 999.0,
    "contracts": {
        "GOLDM": {
            "trading_unit_g": 100,
            "quote_unit_g": 10,
            "purity": 995,
            "expiry_window_days": [3, 5],
            "listing_from": None,
            "lot_size_units": None,
            "tick_size_inr": None,
            "tender_period": None,
        },
        "GOLDTEN": {
            "trading_unit_g": 10,
            "quote_unit_g": 10,
            "purity": 999,
            "expiry_window_days": [27, 31],
            "listing_from": "2025-01-01",
            "lot_size_units": 1,
            "tick_size_inr": 1.0,
            "tender_period": 0,
        },
    },
}


def _write(tmp_path: Path, payload: Any, name: str = "contracts.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _mutated(**changes: Any) -> dict[str, Any]:
    """Deep-copy VALID_CONTRACTS and apply ``changes`` to the GOLDM block."""
    payload = copy.deepcopy(VALID_CONTRACTS)
    payload["contracts"]["GOLDM"].update(changes)
    return payload


# ------------------------------------------------------------------ happy path (repo file)


def test_repo_contracts_yaml_loads() -> None:
    cfg = load_contracts()
    assert isinstance(cfg, ContractsConfig)
    assert cfg.basis_purity == 999.0
    assert cfg.symbols == ("GOLDM", "GOLDTEN", "GOLDGUINEA", "GOLDPETAL")
    goldm = cfg.spec("GOLDM")
    assert goldm.trading_unit_g == 100
    assert goldm.quote_unit_g == 10
    assert goldm.purity == 995
    assert goldm.expiry_window_days == (3, 5)
    assert goldm.listing_from is None
    assert cfg.spec("GOLDTEN").listing_from == date(2025, 1, 1)
    assert cfg.spec("GOLDPETAL").quote_unit_g == 1


def test_default_path_points_at_repo_config_dir() -> None:
    assert (DEFAULT_CONFIG_DIR / "contracts.yaml").is_file()


def test_unverified_fields_reported(tmp_path: Path) -> None:
    cfg = load_contracts(_write(tmp_path, VALID_CONTRACTS))
    assert cfg.spec("GOLDM").unverified_fields() == (
        "lot_size_units",
        "tick_size_inr",
        "tender_period",
    )
    assert cfg.spec("GOLDTEN").unverified_fields() == ()


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = load_contracts(_write(tmp_path, VALID_CONTRACTS))
    with pytest.raises(ValueError):
        cfg.spec("GOLDM").purity = 1  # type: ignore[misc]


def test_unknown_symbol_lookup_raises(tmp_path: Path) -> None:
    cfg = load_contracts(_write(tmp_path, VALID_CONTRACTS))
    with pytest.raises(ConfigError, match="SILVERM"):
        cfg.spec("SILVERM")


# ------------------------------------------------------------------ file-level failures


def test_missing_file_raises_with_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="nope.yaml"):
        load_contracts(missing)


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "contracts.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_contracts(path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "contracts.yaml"
    path.write_text("contracts: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_contracts(path)


# ------------------------------------------------------------------ field-level failures


def test_missing_required_field_names_field(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_CONTRACTS)
    del payload["contracts"]["GOLDM"]["quote_unit_g"]
    with pytest.raises(ConfigError, match=r"contracts\.GOLDM\.quote_unit_g"):
        load_contracts(_write(tmp_path, payload))


def test_missing_verify_key_is_still_required(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_CONTRACTS)
    del payload["contracts"]["GOLDM"]["lot_size_units"]
    with pytest.raises(ConfigError, match="lot_size_units"):
        load_contracts(_write(tmp_path, payload))


def test_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="lot_size"):
        load_contracts(_write(tmp_path, _mutated(lot_size=1)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quote_unit_g", 0),
        ("trading_unit_g", -100),
        ("purity", 0),
        ("purity", 1001),
        ("lot_size_units", 0),
        ("tick_size_inr", -1),
        ("tender_period", -1),
    ],
)
def test_out_of_range_values_rejected(tmp_path: Path, field: str, value: Any) -> None:
    with pytest.raises(ConfigError, match=field):
        load_contracts(_write(tmp_path, _mutated(**{field: value})))


@pytest.mark.parametrize("window", [[5, 3], [0, 5], [27, 32], [3], [3, 4, 5]])
def test_bad_expiry_window_rejected(tmp_path: Path, window: list[int]) -> None:
    with pytest.raises(ConfigError, match="expiry_window_days"):
        load_contracts(_write(tmp_path, _mutated(expiry_window_days=window)))


def test_bad_listing_date_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="listing_from"):
        load_contracts(_write(tmp_path, _mutated(listing_from="not-a-date")))


def test_lowercase_symbol_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_CONTRACTS)
    payload["contracts"]["goldm "] = payload["contracts"].pop("GOLDM")
    with pytest.raises(ConfigError, match="upper-case"):
        load_contracts(_write(tmp_path, payload))


def test_empty_universe_rejected(tmp_path: Path) -> None:
    payload = {"basis_purity": 999.0, "contracts": {}}
    with pytest.raises(ConfigError, match="contracts"):
        load_contracts(_write(tmp_path, payload))


def test_bad_basis_purity_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_CONTRACTS)
    payload["basis_purity"] = 0
    with pytest.raises(ConfigError, match="basis_purity"):
        load_contracts(_write(tmp_path, payload))


# ================================================================== costs.yaml

VALID_COSTS: dict[str, Any] = {
    "brokerage_inr_per_order": None,
    "exchange_txn_charge_pct": None,
    "ctt_pct_on_sell": None,
    "sebi_fee_pct": None,
    "stamp_duty_pct": None,
    "gst_pct_on_fees": None,
    "slippage": {
        "model": "half_spread_ticks",
        "ticks_per_side": {"GOLDM": 1, "GOLDTEN": 2},
        "thin_day_multiplier": 2.0,
    },
    "stress_multipliers": [0.5, 1.0, 2.0],
}


def _costs(**changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(VALID_COSTS)
    payload.update(changes)
    return payload


def test_repo_costs_yaml_loads() -> None:
    cfg = load_costs()
    assert isinstance(cfg, CostsConfig)
    assert cfg.slippage.model == "half_spread_ticks"
    assert cfg.ticks_for("GOLDPETAL") == 3
    assert cfg.slippage.thin_day_multiplier == 2.0
    assert 1.0 in cfg.stress_multipliers
    # All fee fields are placeholders until verified (costs.yaml header).
    assert cfg.unverified_fields() == CostsConfig.VERIFY_FIELDS


def test_costs_verified_fields_clear_flag(tmp_path: Path) -> None:
    filled = _costs(
        brokerage_inr_per_order=20.0,
        exchange_txn_charge_pct=0.0026,
        ctt_pct_on_sell=0.01,
        sebi_fee_pct=0.0001,
        stamp_duty_pct=0.002,
        gst_pct_on_fees=18.0,
    )
    cfg = load_costs(_write(tmp_path, filled, "costs.yaml"))
    assert cfg.unverified_fields() == ()
    assert cfg.brokerage_inr_per_order == 20.0


def test_costs_missing_fee_key_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_COSTS)
    del payload["ctt_pct_on_sell"]
    with pytest.raises(ConfigError, match="ctt_pct_on_sell"):
        load_costs(_write(tmp_path, payload, "costs.yaml"))


def test_costs_negative_fee_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="sebi_fee_pct"):
        load_costs(_write(tmp_path, _costs(sebi_fee_pct=-0.1), "costs.yaml"))


def test_costs_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="brokerage_pct"):
        load_costs(_write(tmp_path, _costs(brokerage_pct=0.01), "costs.yaml"))


def test_costs_unknown_slippage_model_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_COSTS)
    payload["slippage"]["model"] = "magic"
    with pytest.raises(ConfigError, match=r"slippage\.model"):
        load_costs(_write(tmp_path, payload, "costs.yaml"))


def test_costs_negative_ticks_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_COSTS)
    payload["slippage"]["ticks_per_side"]["GOLDM"] = -1
    with pytest.raises(ConfigError, match="ticks_per_side"):
        load_costs(_write(tmp_path, payload, "costs.yaml"))


def test_costs_lowercase_tick_symbol_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_COSTS)
    payload["slippage"]["ticks_per_side"]["goldm"] = 1
    with pytest.raises(ConfigError, match="upper-case"):
        load_costs(_write(tmp_path, payload, "costs.yaml"))


def test_costs_thin_multiplier_below_one_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_COSTS)
    payload["slippage"]["thin_day_multiplier"] = 0.5
    with pytest.raises(ConfigError, match="thin_day_multiplier"):
        load_costs(_write(tmp_path, payload, "costs.yaml"))


@pytest.mark.parametrize("multipliers", [[], [0.5, 2.0], [0.0, 1.0], [-1.0, 1.0]])
def test_costs_bad_stress_multipliers_rejected(tmp_path: Path, multipliers: list[float]) -> None:
    with pytest.raises(ConfigError, match="stress_multipliers"):
        load_costs(_write(tmp_path, _costs(stress_multipliers=multipliers), "costs.yaml"))


def test_costs_ticks_for_unknown_symbol_raises(tmp_path: Path) -> None:
    cfg = load_costs(_write(tmp_path, VALID_COSTS, "costs.yaml"))
    with pytest.raises(ConfigError, match="GOLDGUINEA"):
        cfg.ticks_for("GOLDGUINEA")


# ================================================================== backtest.yaml

VALID_BACKTEST: dict[str, Any] = {
    "seed": 42,
    "split": {"warmup_days": 60, "train_end": None, "embargo_days": 10, "test_start": None},
    "zscore_window_days": 60,
    "entry_z": 2.0,
    "exit_z": 0.5,
    "stop_z": 4.0,
    "max_hold_days": 15,
    "exit_buffer_days_before_expiry": 5,
    "liquidity": {
        "min_volume_lots": None,
        "min_open_interest_lots": None,
        "max_participation_pct_of_volume": 5,
    },
    "capital_inr": 1_000_000,
    "fill_rule": "next_day_settlement",
}


def _bt(**changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(VALID_BACKTEST)
    payload.update(changes)
    return payload


def _bt_split(**changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(VALID_BACKTEST)
    payload["split"].update(changes)
    return payload


def test_repo_backtest_yaml_loads() -> None:
    cfg = load_backtest()
    assert isinstance(cfg, BacktestConfig)
    assert cfg.seed == 42
    assert cfg.zscore_window_days == 60
    assert cfg.exit_z < cfg.entry_z < cfg.stop_z
    assert cfg.fill_rule == "next_day_settlement"
    assert cfg.liquidity.max_participation_pct_of_volume == 5
    # Dates are not frozen until the data audit (backtest.yaml comment, DECISIONS.md).
    assert cfg.split.is_frozen is False


def test_backtest_split_frozen_when_dates_set(tmp_path: Path) -> None:
    payload = _bt_split(train_end="2026-03-31", test_start="2026-04-15")
    cfg = load_backtest(_write(tmp_path, payload, "backtest.yaml"))
    assert cfg.split.is_frozen is True
    assert cfg.split.train_end == date(2026, 3, 31)
    assert cfg.split.test_start == date(2026, 4, 15)


def test_backtest_test_start_before_train_end_rejected(tmp_path: Path) -> None:
    payload = _bt_split(train_end="2026-04-15", test_start="2026-03-31")
    with pytest.raises(ConfigError, match="after train_end"):
        load_backtest(_write(tmp_path, payload, "backtest.yaml"))


def test_backtest_embargo_shorter_than_configured_rejected(tmp_path: Path) -> None:
    # 2026-03-31 -> 2026-04-05 is 5 calendar days, embargo requires >= 10.
    payload = _bt_split(train_end="2026-03-31", test_start="2026-04-05")
    with pytest.raises(ConfigError, match="embargo_days"):
        load_backtest(_write(tmp_path, payload, "backtest.yaml"))


@pytest.mark.parametrize(
    "thresholds",
    [
        {"entry_z": 0.5, "exit_z": 0.5},  # entry must exceed exit
        {"entry_z": 2.0, "exit_z": 3.0},
        {"entry_z": 4.0, "stop_z": 4.0},  # stop must exceed entry
        {"entry_z": 5.0, "stop_z": 4.0},
    ],
)
def test_backtest_threshold_order_enforced(tmp_path: Path, thresholds: dict[str, float]) -> None:
    with pytest.raises(ConfigError, match="exit_z < entry_z < stop_z"):
        load_backtest(_write(tmp_path, _bt(**thresholds), "backtest.yaml"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", -1),
        ("zscore_window_days", 1),
        ("max_hold_days", 0),
        ("exit_buffer_days_before_expiry", -1),
        ("capital_inr", 0),
        ("fill_rule", "same_day_close"),
    ],
)
def test_backtest_out_of_range_rejected(tmp_path: Path, field: str, value: Any) -> None:
    with pytest.raises(ConfigError, match=field):
        load_backtest(_write(tmp_path, _bt(**{field: value}), "backtest.yaml"))


def test_backtest_participation_over_100_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(VALID_BACKTEST)
    payload["liquidity"]["max_participation_pct_of_volume"] = 150
    with pytest.raises(ConfigError, match="max_participation_pct_of_volume"):
        load_backtest(_write(tmp_path, payload, "backtest.yaml"))


def test_backtest_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="entry_zscore"):
        load_backtest(_write(tmp_path, _bt(entry_zscore=2.0), "backtest.yaml"))


# ================================================================== load_all


def test_load_all_from_repo_config_dir() -> None:
    cfg = load_all()
    assert isinstance(cfg, AppConfig)
    assert cfg.contracts.symbols == ("GOLDM", "GOLDTEN", "GOLDGUINEA", "GOLDPETAL")
    for symbol in cfg.contracts.symbols:
        assert cfg.costs.ticks_for(symbol) >= 0


def test_load_all_requires_ticks_for_every_contract(tmp_path: Path) -> None:
    _write(tmp_path, VALID_CONTRACTS, "contracts.yaml")  # GOLDM + GOLDTEN
    costs = copy.deepcopy(VALID_COSTS)
    del costs["slippage"]["ticks_per_side"]["GOLDTEN"]
    _write(tmp_path, costs, "costs.yaml")
    _write(tmp_path, VALID_BACKTEST, "backtest.yaml")
    with pytest.raises(ConfigError, match="GOLDTEN"):
        load_all(tmp_path)


def test_load_all_reports_missing_file(tmp_path: Path) -> None:
    _write(tmp_path, VALID_CONTRACTS, "contracts.yaml")
    with pytest.raises(ConfigError, match="costs.yaml"):
        load_all(tmp_path)
