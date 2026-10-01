# Interfaces

Column contracts between data/core (Person 1), signals/backtest (Person 2), and the
dashboard (Person 3). Locked sections match code on `main` as of the T06 hand-off
(`prepare_prices`, `pair_spreads`). Proposed sections are the shapes Person 2 will
implement in T09–T10; they do not invent MCX fees or tick sizes.

Session dates are `datetime.date` stored in an object column. They are not
`datetime64[ns, UTC]`: a Bhavcopy row has a session date and no clock. Do not
coerce them in signals or backtest.

## Locked: registry prices

Source: `src/aurumspread/data/registry.py` `PRICE_COLUMNS_ORDER`, after
`parse_bhavcopy_csv` + `validate_day`. Grain: one row per
`(trade_date, symbol, expiry_date)`.

| column | dtype | unit / values |
|---|---|---|
| `trade_date` | object (`datetime.date`) | session date |
| `symbol` | str | `GOLDM`, `GOLDTEN`, `GOLDGUINEA`, `GOLDPETAL` |
| `expiry_date` | object (`datetime.date`) | contract expiry |
| `open_inr` | float64 | INR per quote unit |
| `high_inr` | float64 | INR per quote unit |
| `low_inr` | float64 | INR per quote unit |
| `close_inr` | float64 | INR per quote unit |
| `volume` | float64 | raw Bhavcopy number; unit unverified (lots vs units) |
| `open_interest` | float64 | raw Bhavcopy number; unit unverified |
| `thin` | bool | volume or open interest missing or <= 0 |
| `jump_flag` | bool | day-over-day close jump above `max_daily_jump_pct` |
| `source` | str | raw file path or label |
| `source_row` | int64 | 1-based line in that file (header is line 1) |

## Locked: normalized, lifecycle-annotated prices

Source: `prepare_prices` = registry columns plus `normalize_prices` and
`annotate_lifecycle`. This is the frame signals may read. It is still keyed by
`(symbol, expiry_date)`, not a continuous near-month series.

| column | dtype | unit / values |
|---|---|---|
| `pure_price_inr_per_g` | float64 | INR per gram at `basis_purity` (999) |
| `dte_days` | int64 | calendar days from `trade_date` to `expiry_date` |
| `status` | str | `pre_listing`, `live`, `exit_buffer`, `expired` |
| `tradable` | bool | `status == "live"` (calendar only; liquidity is later) |

## Locked: aligned spread series

Source: `pair_spreads` / `core.carry.PAIR_COLUMNS`. Grain: one row per
`(trade_date, symbol_a, expiry_a, symbol_b, expiry_b)` where both legs were
`live` that day and the DTE gap passed `alignment.max_dte_gap_days`.
`spread_inr_per_g` is NaN when leg B has no carry estimate. A naive gap is never
substituted.

| column | dtype | unit / values |
|---|---|---|
| `trade_date` | object (`datetime.date`) | session date |
| `symbol_a` | str | long leg family |
| `expiry_a` | object (`datetime.date`) | long leg expiry |
| `dte_a` | int64 | calendar days |
| `price_a_inr_per_g` | float64 | INR/g, unadjusted |
| `symbol_b` | str | short leg family |
| `expiry_b` | object (`datetime.date`) | short leg expiry |
| `dte_b` | int64 | calendar days |
| `price_b_inr_per_g` | float64 | INR/g, unadjusted |
| `dte_gap_days` | int64 | `dte_a - dte_b` |
| `carry_b_inr_per_g_per_day` | float64 | leg B carry; NaN if unknown |
| `carry_source` | str | `own` or `pooled` |
| `adj_price_b_inr_per_g` | float64 | leg B shifted to leg A's DTE |
| `naive_spread_inr_per_g` | float64 | unadjusted `price_a - price_b` (audit only) |
| `spread_inr_per_g` | float64 | `price_a - adj_price_b`; NaN if carry missing |

## Locked: T07 signal columns

Added by `signals.build_spread_signals` on a copy of the aligned spread frame.
Same grain. Each series is the contract pair
`(symbol_a, expiry_a, symbol_b, expiry_b)` sorted by `trade_date`. Row t uses
only that series' rows in the closed window ending at t (length
`zscore_window_days`, inclusive of t). Sample standard deviation uses `ddof=1`.
Incomplete windows, a NaN spread inside the window, or zero sigma yield NaN
z-score. Percentile is still defined when sigma is zero.

| column | dtype | unit / values |
|---|---|---|
| `spread_mean_inr_per_g` | float64 | trailing window mean of `spread_inr_per_g` |
| `spread_sigma_inr_per_g` | float64 | trailing sample std (`ddof=1`) |
| `zscore` | float64 | `(spread - mean) / sigma`; NaN if undefined |
| `percentile_rank` | float64 | fraction of the window `<=` the current spread, in `(0, 1]` |

## Locked: T08 round-trip cost

Returned by `backtest.estimate_round_trip_cost`. Not a table. INR fields are
`float` or `None`. `None` means a `verify` rate or `tick_size_inr` is null;
it is not zero. `missing` lists those names. `cost_inr_per_g` divides
`total_inr` by the caller-supplied `qty_g`.

| field | dtype | meaning |
|---|---|---|
| `slippage_inr` | float or None | ticks × tick size, thin days multiplied, both legs, entry and exit |
| `brokerage_inr` | float or None | flat INR per order × 4 |
| `exchange_inr` | float or None | `exchange_txn_charge_pct / 100` × traded value, every fill |
| `ctt_inr` | float or None | sell fills only |
| `sebi_inr` | float or None | every fill |
| `stamp_inr` | float or None | buy fills only |
| `gst_inr` | float or None | `gst_pct_on_fees / 100` × (brokerage + exchange + SEBI) |
| `total_inr` | float or None | sum of the rows above, after `multiplier` |
| `cost_inr_per_g` | float or None | `total_inr / qty_g` |
| `missing` | tuple of str | unverified inputs |
| `multiplier` | float | stress multiple |

## Locked: T08 sized pair

Returned by `backtest.size_pair`. Grams are signed (long positive). While
`lot_size_units` is null, lot counts and grams are `None` and `missing` names
the symbol. Grams per lot = `trading_unit_g * lot_size_units`.

| field | dtype |
|---|---|
| `lots_a`, `lots_b` | int or None |
| `qty_g_a`, `qty_g_b` | float or None |
| `residual_g` | float or None |
| `missing` | tuple of str |

## Locked: trade log (T09)

Returned by `backtest.walk_forward` as `WalkForwardResult.trades`. One row per
closed round trip. A signal on day t is filled on the next session from
`core.calendar.TradingCalendar` (not the next pair-row in the signal frame).
`cost_inr` is None when a fee or tick size is still null; the engine
does not open that trade. Dates are `datetime.date`.

Skipped entries are rows in `WalkForwardResult.skips` with `reason` in
`warmup`, `embargo`, `test_locked`, `not_live`, `liquidity`, `unverified_lots`,
`unverified_costs`, `no_next_session`, `missing_fill_bar`. Counts by reason are
`WalkForwardResult.skip_counts` and `RunManifest.skip_counts`.

| column | dtype | unit / values |
|---|---|---|
| `trade_id` | int | unique within a run, starting at 1 |
| `symbol_a` | str | |
| `expiry_a` | object (`datetime.date`) | |
| `symbol_b` | str | |
| `expiry_b` | object (`datetime.date`) | |
| `signal_date` | object (`datetime.date`) | close t that opened the trade |
| `entry_fill_date` | object (`datetime.date`) | next **trading** session after `signal_date` |
| `exit_signal_date` | object (`datetime.date`) | |
| `exit_fill_date` | object (`datetime.date`) | next session, or the same day for `exit_buffer` |
| `side` | str | `long_spread` (buy A, sell B) or `short_spread` |
| `qty_g_a` | float | signed grams |
| `qty_g_b` | float | signed grams |
| `residual_g` | float | `qty_g_a + qty_g_b` |
| `entry_fill_a_inr_per_g` | float | |
| `entry_fill_b_inr_per_g` | float | |
| `exit_fill_a_inr_per_g` | float | |
| `exit_fill_b_inr_per_g` | float | |
| `gross_pnl_inr` | float | `sum qty_g * price change` |
| `cost_inr` | float or None | fees + slippage, entry and exit |
| `net_pnl_inr` | float or None | `gross_pnl_inr - cost_inr` |
| `exit_reason` | str | `exit_z`, `stop_z`, `max_hold`, `exit_buffer` |

## Locked: run manifest (T09)

`RunManifest` has no timestamp and no git SHA. `config_sha256` covers backtest,
costs, contracts, `target_g`, the cost multiplier, `allow_test`, and the
trading-calendar dates used for fills. `inputs_sha256` covers the signal frame.
`skip_counts` is a sorted tuple of `(reason, n)` pairs. Git SHA and wall clock
belong on `RunStamp` from `backtest.stamp_run` at the I/O edge only.

## Locked: daily attribution (T10)

PRD section 8. Grain: one row per session while a book is open.
Identity to test: `total_pnl_inr = beta_inr + alpha_inr + cost_inr + residual_inr`.
Returned by `backtest.walk_forward` as `WalkForwardResult.attribution` and
standalone by `backtest.compute_daily_attribution`.

`cost_inr` is signed (`<= 0.0`), representing the drag of fees and slippage
incurred on that session. `residual_inr` captures any unmodelled pricing mismatch
or data gaps (`total_pnl_inr - (beta_inr + alpha_inr + cost_inr)`), which is 0.0
under exact mark-to-market.

`dRef` (the daily reference gold price change in INR/g) defaults to 0.0 (where all
gross P&L is alpha) or can be passed as a series/mapping/constant or reference
symbol (e.g. `GOLDM`). Since `beta_inr + alpha_inr = sum g_i * dP_i` for any
`dRef`, the identity holds unconditionally.

| column | dtype | unit / values |
|---|---|---|
| `trade_date` | object (`datetime.date`) | session date |
| `beta_inr` | float64 | `(sum g_i) * dRef` (signed residual grams × reference move) |
| `alpha_inr` | float64 | `sum g_i * (dP_i - dRef)` (relative-value spread return) |
| `carry_inr` | float64 | subset of alpha, reported separately; not added again |
| `cost_inr` | float64 | signed fees + slippage incurred that day (`<= 0.0`) |
| `residual_inr` | float64 | `total_pnl_inr - (beta_inr + alpha_inr + cost_inr)` |
| `total_pnl_inr` | float64 | `sum g_i * dP_i + cost_inr` |

## Open questions

1. Person 1 / Person 3: keep `datetime.date` in object columns, or does the
   dashboard need `datetime64[ns]` with no timezone? Signals will not convert.
2. Person 1: `volume` and `open_interest` units are still unverified
   (`docs/DATA_CONTRACT.md`). T08 lot and participation math cannot treat them
   as lots until that is confirmed.
3. Person 1: `tick_size_inr` and `lot_size_units` are null on every contract.
   Slippage in `costs.yaml` is in ticks and cannot become INR/g until those
   fields are filled from MCX. Do not substitute a guess.
4. Person 3: `percentile_rank` is a fraction in `(0, 1]`, not 0–100.
5. Person 1 / Person 3: which series is `dRef` for attribution? Resolved in T10:
   `dRef` defaults to 0.0 (or front GOLDM) and accepts any external series/mapping
   without altering total gross P&L (`beta + alpha` is invariant to `dRef`).
