"""Tests for validation configuration and model loading (Task T11.a)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from aurumspread.backtest.validation import (
    HurdlesConfig,
    SignificanceConfig,
    ValidationConfig,
    ValidationLoadError,
    compute_validation_sha256,
    load_validation,
)


def test_load_validation_default_file() -> None:
    """Default config/validation.yaml loads properly with all placeholders."""
    cfg = load_validation()
    assert isinstance(cfg, ValidationConfig)
    assert cfg.annualization_factor == 250
    assert cfg.risk_free_rate_pct == 6.5
    assert cfg.subtract_risk_free is False
    assert cfg.min_trades == 30
    assert cfg.thresholds_provisional is True

    # Hurdles
    assert cfg.hurdles.min_sharpe == 1.0
    assert cfg.hurdles.fatal_sharpe == 0.0
    assert cfg.hurdles.max_drawdown_pct == 15.0
    assert cfg.hurdles.fatal_drawdown_pct == 25.0
    assert cfg.hurdles.min_hit_rate_pct == 50.0
    assert cfg.hurdles.max_cost_drag_pct == 35.0
    assert cfg.hurdles.fatal_cost_drag_pct == 100.0
    assert cfg.hurdles.min_stability_ratio == 0.60

    # Significance
    assert cfg.significance.method == "stationary_bootstrap"
    assert cfg.significance.bootstrap_samples == 2000
    assert cfg.significance.block_size_days == 5
    assert cfg.significance.ci_level == 0.95
    assert cfg.significance.p_value_threshold == 0.05
    assert cfg.significance.fatal_p_value_threshold == 0.20


def test_validation_sha256_deterministic() -> None:
    """validation_sha256 is a 64-character deterministic hex string."""
    cfg1 = load_validation()
    cfg2 = load_validation()
    hash1 = cfg1.validation_sha256()
    hash2 = compute_validation_sha256(cfg2)
    assert len(hash1) == 64
    assert hash1 == hash2

    # Mutating a threshold changes the sha256
    cfg_mut = ValidationConfig(
        annualization_factor=252,
        hurdles=HurdlesConfig(min_sharpe=1.5),
        significance=SignificanceConfig(),
    )
    assert cfg_mut.validation_sha256() != hash1


def test_validation_config_extra_forbidden() -> None:
    """Unknown keys are rejected under extra='forbid'."""
    with pytest.raises(ValidationError):
        ValidationConfig.model_validate({"unknown_key": 123})

    with pytest.raises(ValidationError):
        HurdlesConfig.model_validate({"unknown_hurdle": 0.5})


def test_load_validation_missing_file(tmp_path: Path) -> None:
    """Missing file raises ValidationLoadError."""
    missing = tmp_path / "nonexistent.yaml"
    with pytest.raises(ValidationLoadError, match="not found"):
        load_validation(missing)


def test_load_validation_invalid_yaml(tmp_path: Path) -> None:
    """Invalid YAML syntax raises ValidationLoadError."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("validation: [unclosed list", encoding="utf-8")
    with pytest.raises(ValidationLoadError, match="invalid YAML"):
        load_validation(bad_yaml)
