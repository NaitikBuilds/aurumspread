"""Round-trip transaction costs (docs/COST_MODEL.md items 1–5).

Slippage for ``half_spread_ticks`` is ``ticks_per_side * tick_size_inr``,
doubled on thin days via ``thin_day_multiplier``. Fee rates come only from
``CostsConfig``. A null rate or a null ``tick_size_inr`` leaves the INR total
as ``None`` and names the gap in ``missing``. Null is never treated as zero.

GST is ``gst_pct_on_fees / 100`` times brokerage + exchange charge + SEBI fee.
CTT and stamp are taxes, not included in that base. COST_MODEL.md item 2 says
"GST on fees" and does not list the base; this split is the reading used here
until a contract note confirms it.

``pct`` fields are per 100 of traded value (``CostsConfig``). Traded value is
the quoted INR notional of the fill, not the purity-adjusted INR/g price.
The stress multiplier scales every INR component.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aurumspread.config import ConfigError, ContractsConfig, CostsConfig

Side = Literal["buy", "sell"]
SpreadSide = Literal["long_spread", "short_spread"]

_FEE_FIELDS = (
    "brokerage_inr_per_order",
    "exchange_txn_charge_pct",
    "ctt_pct_on_sell",
    "sebi_fee_pct",
    "stamp_duty_pct",
    "gst_pct_on_fees",
)


@dataclass(frozen=True)
class OrderCost:
    """INR cost of one fill of one leg. ``total_inr`` is ``None`` if ``missing``."""

    symbol: str
    side: Side
    slippage_inr: float | None
    brokerage_inr: float | None
    exchange_inr: float | None
    ctt_inr: float | None
    sebi_inr: float | None
    stamp_inr: float | None
    gst_inr: float | None
    total_inr: float | None
    missing: tuple[str, ...]
    multiplier: float


@dataclass(frozen=True)
class RoundTripCost:
    """Four fills (entry and exit of both legs). ``cost_inr_per_g`` uses ``qty_g``."""

    slippage_inr: float | None
    brokerage_inr: float | None
    exchange_inr: float | None
    ctt_inr: float | None
    sebi_inr: float | None
    stamp_inr: float | None
    gst_inr: float | None
    total_inr: float | None
    cost_inr_per_g: float | None
    missing: tuple[str, ...]
    multiplier: float
    side: SpreadSide


def order_cost_inr(
    symbol: str,
    side: Side,
    traded_value_inr: float,
    *,
    thin: bool,
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float = 1.0,
) -> OrderCost:
    """Cost of one buy or sell. CTT applies only to sells; stamp only to buys.

    Slippage model other than ``half_spread_ticks`` raises ``NotImplementedError``.
    docs/COST_MODEL.md item 1 specifies ticks times tick size and does not give
    a formula for ``pct_of_price`` or ``volume_scaled``.
    """
    _check_multiplier(multiplier)
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if traded_value_inr < 0:
        raise ValueError(f"traded_value_inr must be >= 0, got {traded_value_inr}")

    missing: list[str] = []
    rates = {name: getattr(costs, name) for name in _FEE_FIELDS}
    brokerage = _required(rates["brokerage_inr_per_order"], "brokerage_inr_per_order", missing)
    exchange = _pct_of(
        traded_value_inr, rates["exchange_txn_charge_pct"], "exchange_txn_charge_pct", missing
    )
    ctt = (
        _pct_of(traded_value_inr, rates["ctt_pct_on_sell"], "ctt_pct_on_sell", missing)
        if side == "sell"
        else 0.0
    )
    sebi = _pct_of(traded_value_inr, rates["sebi_fee_pct"], "sebi_fee_pct", missing)
    stamp = (
        _pct_of(traded_value_inr, rates["stamp_duty_pct"], "stamp_duty_pct", missing)
        if side == "buy"
        else 0.0
    )
    gst = _gst(brokerage, exchange, sebi, rates["gst_pct_on_fees"], missing)
    slippage = _slippage_inr(symbol, thin=thin, costs=costs, contracts=contracts, missing=missing)

    parts = (slippage, brokerage, exchange, ctt, sebi, stamp, gst)
    if any(part is None for part in parts):
        total = None
    else:
        assert slippage is not None
        assert brokerage is not None
        assert exchange is not None
        assert ctt is not None
        assert sebi is not None
        assert stamp is not None
        assert gst is not None
        slippage *= multiplier
        brokerage *= multiplier
        exchange *= multiplier
        ctt *= multiplier
        sebi *= multiplier
        stamp *= multiplier
        gst *= multiplier
        total = slippage + brokerage + exchange + ctt + sebi + stamp + gst
    return OrderCost(
        symbol=symbol,
        side=side,
        slippage_inr=slippage,
        brokerage_inr=brokerage,
        exchange_inr=exchange,
        ctt_inr=ctt,
        sebi_inr=sebi,
        stamp_inr=stamp,
        gst_inr=gst,
        total_inr=total,
        missing=tuple(sorted(set(missing))),
        multiplier=multiplier,
    )


def estimate_round_trip_cost(
    symbol_a: str,
    symbol_b: str,
    traded_value_a_inr: float,
    traded_value_b_inr: float,
    qty_g: float,
    *,
    thin_a: bool,
    thin_b: bool,
    costs: CostsConfig,
    contracts: ContractsConfig,
    multiplier: float = 1.0,
    side: SpreadSide = "long_spread",
) -> RoundTripCost:
    """Pre-trade round trip: entry and exit of both legs at the given traded values.

    The same traded value is used for entry and exit because the exit price is
    not known yet (COST_MODEL.md item 3: cost before entry). A long spread buys
    A and sells B, then sells A and buys B. ``qty_g`` is the gram quantity the
    per-gram cost is quoted on (leg A's absolute grams).
    """
    if side not in ("long_spread", "short_spread"):
        raise ValueError(f"side must be 'long_spread' or 'short_spread', got {side!r}")
    if qty_g <= 0:
        raise ValueError(f"qty_g must be > 0, got {qty_g}")
    if side == "long_spread":
        fills: tuple[tuple[Side, str, float, bool], ...] = (
            ("buy", symbol_a, traded_value_a_inr, thin_a),
            ("sell", symbol_b, traded_value_b_inr, thin_b),
            ("sell", symbol_a, traded_value_a_inr, thin_a),
            ("buy", symbol_b, traded_value_b_inr, thin_b),
        )
    else:
        fills = (
            ("sell", symbol_a, traded_value_a_inr, thin_a),
            ("buy", symbol_b, traded_value_b_inr, thin_b),
            ("buy", symbol_a, traded_value_a_inr, thin_a),
            ("sell", symbol_b, traded_value_b_inr, thin_b),
        )
    orders = [
        order_cost_inr(
            symbol,
            fill_side,
            traded_value,
            thin=thin,
            costs=costs,
            contracts=contracts,
            multiplier=multiplier,
        )
        for fill_side, symbol, traded_value, thin in fills
    ]
    return _sum_orders(orders, qty_g=qty_g, multiplier=multiplier, side=side)


def _sum_orders(
    orders: list[OrderCost],
    *,
    qty_g: float,
    multiplier: float,
    side: SpreadSide,
) -> RoundTripCost:
    missing = tuple(sorted({name for order in orders for name in order.missing}))

    def _add(attr: str) -> float | None:
        values = [getattr(order, attr) for order in orders]
        if any(value is None for value in values):
            return None
        return float(sum(values))

    total = _add("total_inr")
    return RoundTripCost(
        slippage_inr=_add("slippage_inr"),
        brokerage_inr=_add("brokerage_inr"),
        exchange_inr=_add("exchange_inr"),
        ctt_inr=_add("ctt_inr"),
        sebi_inr=_add("sebi_inr"),
        stamp_inr=_add("stamp_inr"),
        gst_inr=_add("gst_inr"),
        total_inr=total,
        cost_inr_per_g=None if total is None else total / qty_g,
        missing=missing,
        multiplier=multiplier,
        side=side,
    )


def _check_multiplier(multiplier: float) -> None:
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise TypeError(f"multiplier must be a number, got {multiplier!r}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")


def _required(value: float | None, name: str, missing: list[str]) -> float | None:
    if value is None:
        missing.append(name)
        return None
    return float(value)


def _pct_of(
    traded_value_inr: float,
    pct: float | None,
    name: str,
    missing: list[str],
) -> float | None:
    rate = _required(pct, name, missing)
    if rate is None:
        return None
    return traded_value_inr * rate / 100.0


def _gst(
    brokerage: float | None,
    exchange: float | None,
    sebi: float | None,
    gst_pct: float | None,
    missing: list[str],
) -> float | None:
    rate = _required(gst_pct, "gst_pct_on_fees", missing)
    if brokerage is None or exchange is None or sebi is None or rate is None:
        return None
    return (brokerage + exchange + sebi) * rate / 100.0


def _slippage_inr(
    symbol: str,
    *,
    thin: bool,
    costs: CostsConfig,
    contracts: ContractsConfig,
    missing: list[str],
) -> float | None:
    if costs.slippage.model != "half_spread_ticks":
        raise NotImplementedError(
            f"slippage model {costs.slippage.model!r} has no formula in docs/COST_MODEL.md; "
            "only half_spread_ticks (ticks_per_side * tick_size_inr) is implemented"
        )
    try:
        tick_size = contracts.spec(symbol).tick_size_inr
    except ConfigError:
        raise
    if tick_size is None:
        missing.append(f"tick_size_inr:{symbol}")
        return None
    ticks = costs.ticks_for(symbol)
    thin_multiplier = costs.slippage.thin_day_multiplier if thin else 1.0
    return float(ticks * tick_size * thin_multiplier)
