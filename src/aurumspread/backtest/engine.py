"""Walk-forward engine (docs/BACKTEST_PROTOCOL.md rules 1–7, task T09).

On day t the engine may read that day's row only. A signal at the close of t
is filled on the next session date in the frame. Exit-buffer exits fill on
the same day, because that session is already inside the forced-exit window.

An entry is skipped when lot size or fee inputs are still null, when either
leg fails the liquidity check, or when the date is warmup, embargo, or an
unopened TEST window. Null costs are not treated as zero.

The function is pure: the same inputs always return the same trades, skips,
and manifest. It does not read the clock or the git repository.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import pandas as pd

from aurumspread.backtest.costs import order_cost_inr
from aurumspread.backtest.liquidity import passes_liquidity
from aurumspread.backtest.manifest import RunManifest, config_sha256, frame_sha256
from aurumspread.backtest.sizing import SizedPair, size_pair
from aurumspread.config import BacktestConfig, ContractsConfig, CostsConfig

Side = Literal["long_spread", "short_spread"]
Phase = Literal["warmup", "train", "embargo", "test"]

TRADE_COLUMNS = (
    "trade_id",
    "symbol_a",
    "expiry_a",
    "symbol_b",
    "expiry_b",
    "signal_date",
    "entry_fill_date",
    "exit_signal_date",
    "exit_fill_date",
    "side",
    "qty_g_a",
    "qty_g_b",
    "residual_g",
    "entry_fill_a_inr_per_g",
    "entry_fill_b_inr_per_g",
    "exit_fill_a_inr_per_g",
    "exit_fill_b_inr_per_g",
    "gross_pnl_inr",
    "cost_inr",
    "net_pnl_inr",
    "exit_reason",
)
SKIP_COLUMNS = (
    "trade_date",
    "symbol_a",
    "expiry_a",
    "symbol_b",
    "expiry_b",
    "reason",
)
_PAIR = ("symbol_a", "expiry_a", "symbol_b", "expiry_b")
_REQUIRED = (
    *_PAIR,
    "trade_date",
    "zscore",
    "price_a_inr_per_g",
    "price_b_inr_per_g",
    "volume_a",
    "open_interest_a",
    "volume_b",
    "open_interest_b",
    "thin_a",
    "thin_b",
    "status_a",
    "status_b",
)


@dataclass(frozen=True)
class WalkForwardResult:
    """Trades, skipped entries, and the manifest that identifies the run."""

    trades: pd.DataFrame
    skips: pd.DataFrame
    manifest: RunManifest


@dataclass
class _PendingEntry:
    pair: tuple
    side: Side
    sized: SizedPair
    signal_date: date
    fill_date: date


@dataclass
class _Position:
    pair: tuple
    side: Side
    sized: SizedPair
    signal_date: date
    entry_fill_date: date
    entry_a: float
    entry_b: float
    thin_a: bool
    thin_b: bool
    exit_reason: str | None = None
    exit_signal_date: date | None = None
    exit_fill_date: date | None = None


def walk_forward(
    signals: pd.DataFrame,
    *,
    backtest: BacktestConfig,
    costs: CostsConfig,
    contracts: ContractsConfig,
    target_g: float,
    multiplier: float = 1.0,
    allow_test: bool = False,
    git_commit: str | None = None,
) -> WalkForwardResult:
    """Run the day loop on a signal frame that already contains z-scores.

    ``target_g`` is the gram target passed to :func:`size_pair`. ``allow_test``
    may be true only after ``backtest.split`` has frozen train and test dates.
    """
    if allow_test and not backtest.split.is_frozen:
        raise ValueError("allow_test requires frozen train_end and test_start")
    if target_g <= 0:
        raise ValueError(f"target_g must be > 0, got {target_g}")
    missing = [column for column in _REQUIRED if column not in signals.columns]
    if missing:
        raise KeyError(f"signals frame lacks columns {missing}")

    frame = signals.copy()
    frame["trade_date"] = frame["trade_date"].map(_as_date)
    for column in ("expiry_a", "expiry_b"):
        frame[column] = frame[column].map(_as_date)
    dates = sorted(set(frame["trade_date"]))
    rows = {
        _pair_key(row): row
        for _, row in frame.sort_values(["trade_date", *_PAIR], kind="mergesort").iterrows()
    }
    pairs = sorted({key[1:] for key in rows})

    pending: dict[tuple, _PendingEntry] = {}
    open_positions: dict[tuple, _Position] = {}
    trades: list[dict] = []
    skips: list[dict] = []

    for today in dates:
        _apply_entries(today, rows, pending, open_positions, skips)
        _apply_scheduled_exits(today, rows, open_positions, trades, costs, contracts, multiplier)
        _force_or_schedule_exits(
            today, dates, rows, open_positions, trades, costs, contracts, backtest, multiplier
        )
        _schedule_entries(
            today,
            dates,
            rows,
            pairs,
            pending,
            open_positions,
            skips,
            backtest,
            costs,
            contracts,
            target_g,
            multiplier,
            allow_test,
        )

    manifest = RunManifest(
        git_commit=git_commit,
        config_sha256=config_sha256(
            backtest,
            costs,
            contracts,
            target_g=target_g,
            multiplier=multiplier,
            allow_test=allow_test,
        ),
        inputs_sha256=frame_sha256(frame),
        start_date=dates[0] if dates else None,
        end_date=dates[-1] if dates else None,
        seed=backtest.seed,
        n_trades=len(trades),
        n_skips=len(skips),
        split_frozen=backtest.split.is_frozen,
    )
    return WalkForwardResult(
        trades=_as_frame(trades, TRADE_COLUMNS),
        skips=_as_frame(skips, SKIP_COLUMNS),
        manifest=manifest,
    )


def _schedule_entries(
    today: date,
    dates: list[date],
    rows: dict,
    pairs: list[tuple],
    pending: dict[tuple, _PendingEntry],
    open_positions: dict[tuple, _Position],
    skips: list[dict],
    backtest: BacktestConfig,
    costs: CostsConfig,
    contracts: ContractsConfig,
    target_g: float,
    multiplier: float,
    allow_test: bool,
) -> None:
    phase = _phase(today, dates, backtest)
    for pair in pairs:
        if pair in open_positions or pair in pending:
            continue
        row = rows.get((today, *pair))
        if row is None:
            continue
        zscore = _finite(row["zscore"])
        if zscore is None or abs(zscore) < backtest.entry_z:
            continue
        side: Side = "short_spread" if zscore > 0 else "long_spread"
        reason = _entry_block(
            row, phase, allow_test, side, backtest, costs, contracts, target_g, multiplier
        )
        if reason is not None:
            skips.append(_skip(today, pair, reason))
            continue
        fill_date = _next_date(dates, today)
        if fill_date is None:
            skips.append(_skip(today, pair, "no_next_session"))
            continue
        sized = size_pair(pair[0], pair[2], target_g, contracts, side=side)
        pending[pair] = _PendingEntry(
            pair=pair,
            side=side,
            sized=sized,
            signal_date=today,
            fill_date=fill_date,
        )


def _entry_block(
    row: pd.Series,
    phase: Phase,
    allow_test: bool,
    side: Side,
    backtest: BacktestConfig,
    costs: CostsConfig,
    contracts: ContractsConfig,
    target_g: float,
    multiplier: float,
) -> str | None:
    if phase == "warmup":
        return "warmup"
    if phase == "embargo":
        return "embargo"
    if phase == "test" and not allow_test:
        return "test_locked"
    if row["status_a"] != "live" or row["status_b"] != "live":
        return "not_live"
    if (
        not passes_liquidity(
            float(row["volume_a"]), float(row["open_interest_a"]), backtest.liquidity
        ).passed
        or not passes_liquidity(
            float(row["volume_b"]), float(row["open_interest_b"]), backtest.liquidity
        ).passed
    ):
        return "liquidity"
    sized = size_pair(str(row["symbol_a"]), str(row["symbol_b"]), target_g, contracts, side=side)
    if not sized.sized or sized.qty_g_a is None:
        return "unverified_lots"
    # Cost is checked before the order is sent (COST_MODEL.md item 3).
    entry_cost = _leg_costs(
        side,
        closing=False,
        price_a=float(row["price_a_inr_per_g"]),
        price_b=float(row["price_b_inr_per_g"]),
        qty_g_a=sized.qty_g_a,
        qty_g_b=sized.qty_g_b or 0.0,
        thin_a=bool(row["thin_a"]),
        thin_b=bool(row["thin_b"]),
        symbol_a=str(row["symbol_a"]),
        symbol_b=str(row["symbol_b"]),
        costs=costs,
        contracts=contracts,
        multiplier=multiplier,
    )
    if entry_cost is None:
        return "unverified_costs"
    return None


def _apply_entries(
    today: date,
    rows: dict,
    pending: dict[tuple, _PendingEntry],
    open_positions: dict[tuple, _Position],
    skips: list[dict],
) -> None:
    for pair, order in list(pending.items()):
        if order.fill_date != today:
            continue
        pending.pop(pair)
        row = rows.get((today, *pair))
        if row is None:
            skips.append(_skip(today, pair, "missing_fill_bar"))
            continue
        open_positions[pair] = _Position(
            pair=pair,
            side=order.side,
            sized=order.sized,
            signal_date=order.signal_date,
            entry_fill_date=today,
            entry_a=float(row["price_a_inr_per_g"]),
            entry_b=float(row["price_b_inr_per_g"]),
            thin_a=bool(row["thin_a"]),
            thin_b=bool(row["thin_b"]),
        )


def _apply_scheduled_exits(
    today: date,
    rows: dict,
    open_positions: dict[tuple, _Position],
    trades: list[dict],
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float,
) -> None:
    for pair, position in list(open_positions.items()):
        if position.exit_fill_date != today:
            continue
        row = rows.get((today, *pair))
        if row is None:
            continue
        _close(position, row, trades, costs, contracts, multiplier)
        del open_positions[pair]


def _force_or_schedule_exits(
    today: date,
    dates: list[date],
    rows: dict,
    open_positions: dict[tuple, _Position],
    trades: list[dict],
    costs: CostsConfig,
    contracts: ContractsConfig,
    backtest: BacktestConfig,
    multiplier: float,
) -> None:
    for pair, position in list(open_positions.items()):
        if position.exit_reason is not None:
            continue
        row = rows.get((today, *pair))
        if row is None:
            continue
        held = sum(1 for day in dates if position.entry_fill_date <= day <= today)
        reason = _exit_reason(
            _finite(row["zscore"]),
            held,
            str(row["status_a"]),
            str(row["status_b"]),
            backtest,
        )
        if reason is None:
            continue
        if reason == "exit_buffer" or _next_date(dates, today) is None:
            position.exit_reason = reason
            position.exit_signal_date = today
            position.exit_fill_date = today
            _close(position, row, trades, costs, contracts, multiplier)
            del open_positions[pair]
            continue
        position.exit_reason = reason
        position.exit_signal_date = today
        position.exit_fill_date = _next_date(dates, today)


def _close(
    position: _Position,
    row: pd.Series,
    trades: list[dict],
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float,
) -> None:
    exit_a = float(row["price_a_inr_per_g"])
    exit_b = float(row["price_b_inr_per_g"])
    qty_a = float(position.sized.qty_g_a or 0.0)
    qty_b = float(position.sized.qty_g_b or 0.0)
    gross = qty_a * (exit_a - position.entry_a) + qty_b * (exit_b - position.entry_b)
    entry_cost = _leg_costs(
        position.side,
        closing=False,
        price_a=position.entry_a,
        price_b=position.entry_b,
        qty_g_a=qty_a,
        qty_g_b=qty_b,
        thin_a=position.thin_a,
        thin_b=position.thin_b,
        symbol_a=position.pair[0],
        symbol_b=position.pair[2],
        costs=costs,
        contracts=contracts,
        multiplier=multiplier,
    )
    exit_cost = _leg_costs(
        position.side,
        closing=True,
        price_a=exit_a,
        price_b=exit_b,
        qty_g_a=qty_a,
        qty_g_b=qty_b,
        thin_a=bool(row["thin_a"]),
        thin_b=bool(row["thin_b"]),
        symbol_a=position.pair[0],
        symbol_b=position.pair[2],
        costs=costs,
        contracts=contracts,
        multiplier=multiplier,
    )
    cost = None if entry_cost is None or exit_cost is None else entry_cost + exit_cost
    symbol_a, expiry_a, symbol_b, expiry_b = position.pair
    trades.append(
        {
            "trade_id": len(trades) + 1,
            "symbol_a": symbol_a,
            "expiry_a": expiry_a,
            "symbol_b": symbol_b,
            "expiry_b": expiry_b,
            "signal_date": position.signal_date,
            "entry_fill_date": position.entry_fill_date,
            "exit_signal_date": position.exit_signal_date,
            "exit_fill_date": position.exit_fill_date,
            "side": position.side,
            "qty_g_a": qty_a,
            "qty_g_b": qty_b,
            "residual_g": position.sized.residual_g,
            "entry_fill_a_inr_per_g": position.entry_a,
            "entry_fill_b_inr_per_g": position.entry_b,
            "exit_fill_a_inr_per_g": exit_a,
            "exit_fill_b_inr_per_g": exit_b,
            "gross_pnl_inr": gross,
            "cost_inr": cost,
            "net_pnl_inr": None if cost is None else gross - cost,
            "exit_reason": position.exit_reason,
        }
    )


def _leg_costs(
    side: Side,
    *,
    closing: bool,
    price_a: float,
    price_b: float,
    qty_g_a: float,
    qty_g_b: float,
    thin_a: bool,
    thin_b: bool,
    symbol_a: str,
    symbol_b: str,
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float,
) -> float | None:
    # Long entry buys A and sells B. A short entry is the opposite. Closing flips both.
    sell_a = (side == "short_spread") if not closing else (side == "long_spread")
    a_side = "sell" if sell_a else "buy"
    b_side = "buy" if sell_a else "sell"
    legs = (
        (a_side, symbol_a, price_a, qty_g_a, thin_a),
        (b_side, symbol_b, price_b, qty_g_b, thin_b),
    )
    total = 0.0
    for fill_side, symbol, price, qty_g, thin in legs:
        order = order_cost_inr(
            symbol,
            fill_side,
            abs(qty_g) * price,
            thin=thin,
            costs=costs,
            contracts=contracts,
            multiplier=multiplier,
        )
        if order.total_inr is None:
            return None
        total += order.total_inr
    return total


def _exit_reason(
    zscore: float | None,
    held: int,
    status_a: str,
    status_b: str,
    backtest: BacktestConfig,
) -> str | None:
    if status_a != "live" or status_b != "live":
        return "exit_buffer"
    if zscore is not None and abs(zscore) >= backtest.stop_z:
        return "stop_z"
    if held >= backtest.max_hold_days:
        return "max_hold"
    if zscore is not None and abs(zscore) <= backtest.exit_z:
        return "exit_z"
    return None


def _phase(today: date, dates: list[date], backtest: BacktestConfig) -> Phase:
    if dates.index(today) < backtest.split.warmup_days:
        return "warmup"
    split = backtest.split
    if not split.is_frozen or split.train_end is None or split.test_start is None:
        return "train"
    if today <= split.train_end:
        return "train"
    if today < split.test_start:
        return "embargo"
    return "test"


def _next_date(dates: list[date], today: date) -> date | None:
    index = dates.index(today)
    if index + 1 >= len(dates):
        return None
    return dates[index + 1]


def _pair_key(row: pd.Series) -> tuple:
    return (
        _as_date(row["trade_date"]),
        row["symbol_a"],
        _as_date(row["expiry_a"]),
        row["symbol_b"],
        _as_date(row["expiry_b"]),
    )


def _skip(today: date, pair: tuple, reason: str) -> dict:
    symbol_a, expiry_a, symbol_b, expiry_b = pair
    return {
        "trade_date": today,
        "symbol_a": symbol_a,
        "expiry_a": expiry_a,
        "symbol_b": symbol_b,
        "expiry_b": expiry_b,
        "reason": reason,
    }


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()  # type: ignore[arg-type]


def _as_frame(rows: list[dict], columns: tuple[str, ...]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(columns))
    return pd.DataFrame(rows, columns=list(columns))
