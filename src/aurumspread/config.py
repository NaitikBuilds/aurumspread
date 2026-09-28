"""Typed loaders for ``config/*.yaml`` (docs/ARCHITECTURE.md: ``config.py``).

Every tunable number lives in YAML; this module is the only place that reads
those files. Loaders return frozen pydantic models so downstream code cannot
mutate configuration at runtime. Any missing, malformed or unknown value raises
:class:`ConfigError` naming the offending file and field.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ConfigError(ValueError):
    """Raised when a config file is missing, unreadable or fails validation."""


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(loaded).__name__}")
    return loaded


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _validate(model: type[_ModelT], raw: dict[str, Any], path: Path) -> _ModelT:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {_format_errors(exc)}") from exc


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- contracts.yaml


class ContractSpec(_FrozenModel):
    """Static facts about one MCX gold futures family (``config/contracts.yaml``).

    Fields tagged ``verify:true`` in the YAML are not in the problem statement and
    stay ``None`` until a human fills them from the MCX spec. They are required
    keys (so a typo is caught) but may be null.
    """

    VERIFY_FIELDS: ClassVar[tuple[str, ...]] = ("lot_size_units", "tick_size_inr", "tender_period")

    trading_unit_g: float = Field(gt=0, description="Grams per lot actually traded/delivered.")
    quote_unit_g: float = Field(gt=0, description="Grams the quoted price refers to (GOLDM: 10).")
    purity: float = Field(gt=0, le=1000, description="Fineness in parts per thousand.")
    expiry_window_days: tuple[int, int] = Field(
        description="Inclusive day-of-month range in which the contract expires."
    )
    listing_from: date | None = Field(
        description="First trade date; None = listed before the data history starts."
    )
    lot_size_units: int | None = Field(gt=0, description="verify:true - MCX lot size in units.")
    tick_size_inr: float | None = Field(gt=0, description="verify:true - MCX tick size in INR.")
    tender_period: int | None = Field(
        ge=0, description="verify:true - tender period length in days before expiry."
    )

    @field_validator("expiry_window_days")
    @classmethod
    def _check_window(cls, v: tuple[int, int]) -> tuple[int, int]:
        lo, hi = v
        if not 1 <= lo <= hi <= 31:
            raise ValueError(f"must satisfy 1 <= first <= last <= 31, got {list(v)}")
        return v

    def unverified_fields(self) -> tuple[str, ...]:
        """Names of ``verify:true`` fields that are still null."""
        return tuple(name for name in self.VERIFY_FIELDS if getattr(self, name) is None)


class ContractsConfig(_FrozenModel):
    """Whole ``config/contracts.yaml``: pure-gold basis plus the contract universe."""

    basis_purity: float = Field(gt=0, le=1000, description="Common purity basis (PRD 6.1).")
    contracts: dict[str, ContractSpec] = Field(min_length=1)

    @field_validator("contracts")
    @classmethod
    def _check_symbols(cls, v: dict[str, ContractSpec]) -> dict[str, ContractSpec]:
        bad = [s for s in v if not s or s != s.strip().upper()]
        if bad:
            raise ValueError(f"contract symbols must be stripped upper-case, got {bad}")
        return v

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.contracts)

    def spec(self, symbol: str) -> ContractSpec:
        try:
            return self.contracts[symbol]
        except KeyError:
            raise ConfigError(
                f"unknown contract symbol {symbol!r}; configured: {list(self.symbols)}"
            ) from None


def load_contracts(path: Path | str | None = None) -> ContractsConfig:
    """Load and validate ``contracts.yaml`` (default: ``config/contracts.yaml`` in the repo)."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "contracts.yaml"
    return _validate(ContractsConfig, _read_yaml_mapping(resolved), resolved)
