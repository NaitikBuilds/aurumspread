"""Typed loaders for ``config/*.yaml`` (docs/ARCHITECTURE.md: ``config.py``).

Every tunable number lives in YAML; this module is the only place that reads
those files. Loaders return frozen pydantic models so downstream code cannot
mutate configuration at runtime. Any missing, malformed or unknown value raises
:class:`ConfigError` naming the offending file and field.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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


def _check_symbol_keys(v: dict[str, Any]) -> dict[str, Any]:
    bad = [s for s in v if not s or s != s.strip().upper()]
    if bad:
        raise ValueError(f"contract symbols must be stripped upper-case, got {bad}")
    return v


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
        return _check_symbol_keys(v)

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


# --------------------------------------------------------------------------- costs.yaml


class SlippageConfig(_FrozenModel):
    """Slippage assumptions (docs/COST_MODEL.md item 1)."""

    model: Literal["half_spread_ticks", "pct_of_price", "volume_scaled"]
    ticks_per_side: dict[str, float] = Field(
        min_length=1, description="Ticks paid per side per symbol; assumption, stress-tested."
    )
    thin_day_multiplier: float = Field(ge=1, description="Slippage multiplier on thin days.")

    @field_validator("ticks_per_side")
    @classmethod
    def _check_ticks(cls, v: dict[str, float]) -> dict[str, float]:
        _check_symbol_keys(v)
        negative = {s: t for s, t in v.items() if t < 0}
        if negative:
            raise ValueError(f"ticks_per_side must be >= 0, got {negative}")
        return v


class CostsConfig(_FrozenModel):
    """Whole ``config/costs.yaml``. Fee fields are null until verified against a contract note.

    Percentages are expressed as in the YAML (``pct`` = per 100 of notional).
    """

    VERIFY_FIELDS: ClassVar[tuple[str, ...]] = (
        "brokerage_inr_per_order",
        "exchange_txn_charge_pct",
        "ctt_pct_on_sell",
        "sebi_fee_pct",
        "stamp_duty_pct",
        "gst_pct_on_fees",
    )

    brokerage_inr_per_order: float | None = Field(ge=0, description="verify - flat INR per order.")
    exchange_txn_charge_pct: float | None = Field(ge=0, description="verify - MCX schedule.")
    ctt_pct_on_sell: float | None = Field(ge=0, description="verify - CTT, sell side only.")
    sebi_fee_pct: float | None = Field(ge=0, description="verify - SEBI turnover fee.")
    stamp_duty_pct: float | None = Field(ge=0, description="verify - stamp duty (buy side).")
    gst_pct_on_fees: float | None = Field(ge=0, description="verify - GST applied on fees.")
    slippage: SlippageConfig
    stress_multipliers: tuple[float, ...] = Field(
        min_length=1, description="Cost multiples to report; must include 1.0 (base case)."
    )

    @field_validator("stress_multipliers")
    @classmethod
    def _check_multipliers(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        if any(m <= 0 for m in v):
            raise ValueError(f"all multipliers must be > 0, got {list(v)}")
        if 1.0 not in v:
            raise ValueError(f"must include the base case 1.0, got {list(v)}")
        return v

    def unverified_fields(self) -> tuple[str, ...]:
        """Names of fee fields that are still null (results must be flagged until empty)."""
        return tuple(name for name in self.VERIFY_FIELDS if getattr(self, name) is None)

    def ticks_for(self, symbol: str) -> float:
        try:
            return self.slippage.ticks_per_side[symbol]
        except KeyError:
            raise ConfigError(
                f"no slippage ticks configured for {symbol!r}; "
                f"configured: {list(self.slippage.ticks_per_side)}"
            ) from None


def load_costs(path: Path | str | None = None) -> CostsConfig:
    """Load and validate ``costs.yaml`` (default: ``config/costs.yaml`` in the repo)."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "costs.yaml"
    return _validate(CostsConfig, _read_yaml_mapping(resolved), resolved)


# --------------------------------------------------------------------------- backtest.yaml


class SplitConfig(_FrozenModel):
    """``warmup | TRAIN | embargo | TEST`` timeline (docs/BACKTEST_PROTOCOL.md).

    ``train_end`` / ``test_start`` stay null until the data audit freezes them in
    DECISIONS.md; :attr:`is_frozen` tells callers whether a TEST run is allowed.
    """

    warmup_days: int = Field(ge=0)
    train_end: date | None
    embargo_days: int = Field(ge=0)
    test_start: date | None

    @model_validator(mode="after")
    def _check_order(self) -> SplitConfig:
        if self.train_end is not None and self.test_start is not None:
            gap_days = (self.test_start - self.train_end).days
            if gap_days <= 0:
                raise ValueError(
                    f"test_start ({self.test_start}) must be after train_end ({self.train_end})"
                )
            # Calendar-day gap is a lower bound on the embargo whichever day-count is used.
            if gap_days < self.embargo_days:
                raise ValueError(
                    f"test_start - train_end is {gap_days} days, "
                    f"shorter than embargo_days={self.embargo_days}"
                )
        return self

    @property
    def is_frozen(self) -> bool:
        return self.train_end is not None and self.test_start is not None


class LiquidityConfig(_FrozenModel):
    """Liquidity filters; minimums are null until calibrated on TRAIN."""

    min_volume_lots: int | None = Field(ge=0)
    min_open_interest_lots: int | None = Field(ge=0)
    max_participation_pct_of_volume: float = Field(gt=0, le=100)


class AlignmentConfig(_FrozenModel):
    """Expiry alignment and carry adjustment (PRD 6.2)."""

    carry_method: Literal["front_pair", "adjacent_median"]
    pooled_carry_fallback: bool
    max_dte_gap_days: int = Field(ge=0)
    constant_maturity_grid_days: tuple[int, ...] = Field(min_length=1)

    @field_validator("constant_maturity_grid_days")
    @classmethod
    def _check_grid(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if any(d <= 0 for d in v) or list(v) != sorted(set(v)):
            raise ValueError(f"must be strictly increasing positive days, got {list(v)}")
        return v


class BacktestConfig(_FrozenModel):
    """Whole ``config/backtest.yaml``."""

    seed: int = Field(ge=0)
    split: SplitConfig
    zscore_window_days: int = Field(ge=2)
    entry_z: float = Field(gt=0)
    exit_z: float = Field(ge=0)
    stop_z: float = Field(gt=0)
    max_hold_days: int = Field(ge=1)
    exit_buffer_days_before_expiry: int = Field(ge=0)
    alignment: AlignmentConfig
    liquidity: LiquidityConfig
    capital_inr: float = Field(gt=0)
    fill_rule: Literal["next_day_settlement"]
    residual_tolerance_inr: float = Field(
        default=0.01,
        ge=0,
        description="Tolerance in INR for daily attribution residual anomaly flag.",
    )

    @model_validator(mode="after")
    def _check_thresholds(self) -> BacktestConfig:
        if not self.exit_z < self.entry_z < self.stop_z:
            raise ValueError(
                f"need exit_z < entry_z < stop_z, got "
                f"exit_z={self.exit_z}, entry_z={self.entry_z}, stop_z={self.stop_z}"
            )
        return self


def load_backtest(path: Path | str | None = None) -> BacktestConfig:
    """Load and validate ``backtest.yaml`` (default: ``config/backtest.yaml`` in the repo)."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "backtest.yaml"
    return _validate(BacktestConfig, _read_yaml_mapping(resolved), resolved)


# --------------------------------------------------------------------------- data_source.yaml


class QualityConfig(_FrozenModel):
    """Data-quality thresholds (docs/DATA_CONTRACT.md "Validation")."""

    max_daily_jump_pct: float = Field(gt=0, description="Rule 5: flag larger day-over-day moves.")
    universe: tuple[str, ...] = Field(min_length=1, description="Rule 2: symbols to keep.")

    @field_validator("universe")
    @classmethod
    def _check_universe(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        _check_symbol_keys(dict.fromkeys(v))
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate symbols in universe: {list(v)}")
        return v


class DataSourceConfig(_FrozenModel):
    """Bhavcopy endpoint, politeness and quality settings (``config/data_source.yaml``).

    The request shape is unknown until a human verifies it (docs/DATA_CONTRACT.md
    "Open questions"); :attr:`is_configured` gates any network call.
    """

    VERIFY_FIELDS: ClassVar[tuple[str, ...]] = ("url_template",)

    url_template: str | None = Field(description="verify:true - may contain '{date}'.")
    method: Literal["GET", "POST"]
    params_template: dict[str, str] = Field(description="Values may contain '{date}'.")
    headers: dict[str, str]
    request_date_format: str = Field(min_length=1, description="strftime format, DD/MM/YYYY.")
    timeout_s: float = Field(gt=0)
    min_interval_s: float = Field(ge=0, description="Minimum seconds between requests.")
    max_retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)
    raw_dir: Path = Field(description="Relative paths resolve against the repo root.")
    processed_dir: Path = Field(description="Parquet output; relative to the repo root.")
    quality: QualityConfig

    @field_validator("url_template")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError(f"must start with http:// or https://, got {v!r}")
        return v

    @property
    def is_configured(self) -> bool:
        return self.url_template is not None

    @property
    def raw_dir_abs(self) -> Path:
        return _absolute(self.raw_dir)

    @property
    def processed_dir_abs(self) -> Path:
        return _absolute(self.processed_dir)

    def unverified_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.VERIFY_FIELDS if getattr(self, name) is None)


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_data_source(path: Path | str | None = None) -> DataSourceConfig:
    """Load and validate ``data_source.yaml`` (default: ``config/data_source.yaml``)."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_DIR / "data_source.yaml"
    return _validate(DataSourceConfig, _read_yaml_mapping(resolved), resolved)


# --------------------------------------------------------------------------- everything


class AppConfig(_FrozenModel):
    """All configs, cross-checked for consistency."""

    contracts: ContractsConfig
    costs: CostsConfig
    backtest: BacktestConfig
    data_source: DataSourceConfig

    @model_validator(mode="after")
    def _cross_check(self) -> AppConfig:
        missing = [s for s in self.contracts.symbols if s not in self.costs.slippage.ticks_per_side]
        if missing:
            raise ValueError(f"costs.slippage.ticks_per_side lacks contract symbols {missing}")
        unknown = [
            s for s in self.data_source.quality.universe if s not in self.contracts.contracts
        ]
        if unknown:
            raise ValueError(
                f"data_source.quality.universe has symbols not in contracts: {unknown}"
            )
        return self


def load_all(config_dir: Path | str | None = None) -> AppConfig:
    """Load all ``config/*.yaml`` files from one directory."""
    base = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    try:
        return AppConfig(
            contracts=load_contracts(base / "contracts.yaml"),
            costs=load_costs(base / "costs.yaml"),
            backtest=load_backtest(base / "backtest.yaml"),
            data_source=load_data_source(base / "data_source.yaml"),
        )
    except ValidationError as exc:
        raise ConfigError(f"{base}: {_format_errors(exc)}") from exc
