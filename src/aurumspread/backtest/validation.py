"""Validation configuration and models for Task T11 strategy validation battery.

Every threshold is marked as a PLACEHOLDER with TODO comments pending protocol
sign-off. The strategy verdict carries ``thresholds_provisional=True`` while any
placeholder remains.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VALIDATION_CONFIG_PATH = REPO_ROOT / "config" / "validation.yaml"


class ValidationLoadError(ValueError):
    """Raised when validation configuration is missing or invalid."""


class HurdlesConfig(BaseModel):
    """Validation hurdles and fatal thresholds [ALL PLACEHOLDERS]."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_sharpe: float = Field(
        default=1.0,
        description="Minimum annualized Sharpe hurdle [PLACEHOLDER - TODO: quant team]",
    )
    fatal_sharpe: float = Field(
        default=0.0,
        description="Fatal lower bound for Sharpe [PLACEHOLDER - TODO: quant team]",
    )
    max_drawdown_pct: float = Field(
        default=15.0,
        ge=0,
        description="Maximum drawdown % [PLACEHOLDER - TODO: quant team]",
    )
    fatal_drawdown_pct: float = Field(
        default=25.0,
        ge=0,
        description="Fatal drawdown % [PLACEHOLDER - TODO: quant team]",
    )
    min_hit_rate_pct: float = Field(
        default=50.0,
        ge=0,
        le=100,
        description="Minimum winning trades % [PLACEHOLDER - TODO: quant team]",
    )
    max_cost_drag_pct: float = Field(
        default=35.0,
        ge=0,
        description="Maximum cost drag % [PLACEHOLDER - TODO: quant team]",
    )
    fatal_cost_drag_pct: float = Field(
        default=100.0,
        ge=0,
        description="Fatal cost drag % [PLACEHOLDER - TODO: quant team]",
    )
    min_stability_ratio: float = Field(
        default=0.60,
        ge=0,
        description="Minimum train-to-test stability ratio [PLACEHOLDER - TODO: quant team]",
    )


class SignificanceConfig(BaseModel):
    """Statistical significance and multiple-testing configuration [ALL PLACEHOLDERS]."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["stationary_bootstrap", "deflated_sharpe"] = Field(
        default="stationary_bootstrap",
        description="Statistical significance evaluation method [PLACEHOLDER - TODO: quant team]",
    )
    bootstrap_samples: int = Field(
        default=2000,
        gt=0,
        description="Number of bootstrap resamples [PLACEHOLDER - TODO: quant team]",
    )
    block_size_days: int = Field(
        default=5,
        gt=0,
        description="Expected block size in days for bootstrap [PLACEHOLDER - TODO: quant team]",
    )
    ci_level: float = Field(
        default=0.95,
        gt=0,
        lt=1,
        description="Confidence interval coverage [PLACEHOLDER - TODO: quant team]",
    )
    p_value_threshold: float = Field(
        default=0.05,
        gt=0,
        lt=1,
        description="Alpha threshold for significance [PLACEHOLDER - TODO: quant team]",
    )
    fatal_p_value_threshold: float = Field(
        default=0.20,
        gt=0,
        lt=1,
        description="Fatal p-value threshold [PLACEHOLDER - TODO: quant team]",
    )


class ValidationConfig(BaseModel):
    """Strategy validation configuration model (Person 2 owned)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    annualization_factor: int = Field(
        default=250,
        gt=0,
        description="Annual session count [PLACEHOLDER - TODO: quant team; verify 250 vs 252]",
    )
    risk_free_rate_pct: float = Field(
        default=6.5,
        ge=0,
        description="Annual risk-free rate % [PLACEHOLDER - TODO: quant team; verify RBI rate]",
    )
    subtract_risk_free: bool = Field(
        default=False,
        description="Subtract risk-free rate from Sharpe [PLACEHOLDER - TODO: quant team]",
    )
    min_trades: int = Field(
        default=30,
        ge=1,
        description="Minimum closed trades hurdle [PLACEHOLDER - TODO: quant team]",
    )
    thresholds_provisional: bool = Field(
        default=True,
        description="True while any validation hurdle remains a placeholder",
    )
    hurdles: HurdlesConfig = Field(default_factory=HurdlesConfig)
    significance: SignificanceConfig = Field(default_factory=SignificanceConfig)

    def validation_sha256(self) -> str:
        """Compute independent deterministic sha256 hash of this validation configuration."""
        dumped = self.model_dump()
        raw = json.dumps(dumped, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def compute_validation_sha256(config: ValidationConfig) -> str:
    """Return deterministic SHA256 hex digest of a ValidationConfig."""
    return config.validation_sha256()


def load_validation(path: Path | str | None = None) -> ValidationConfig:
    """Load ValidationConfig from a YAML file.

    Defaults to ``config/validation.yaml``.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_VALIDATION_CONFIG_PATH
    if not cfg_path.is_file():
        raise ValidationLoadError(f"Validation config file not found: {cfg_path}")
    try:
        raw_text = cfg_path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ValidationLoadError(f"{cfg_path}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValidationLoadError(
            f"{cfg_path}: top level must be a mapping, got {type(loaded).__name__}"
        )

    data: dict[str, Any] = loaded.get("validation", loaded)
    try:
        return ValidationConfig.model_validate(data)
    except ValidationError as exc:
        raise ValidationLoadError(f"{cfg_path}: validation failed: {exc}") from exc
