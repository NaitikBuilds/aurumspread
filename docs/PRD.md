# AurumEdge PRD v1.1 (repo: aurumspread)
Hack in Hills '26 - Problem Statement 03: Commodity Derivatives Intelligence. Status: ready for development.
Changes vs v1.0 are marked **[NEW]** or **[FIXED]**; see Appendix D.

## 1. Summary
AurumEdge turns MCX Bhavcopy settlement data for GOLDM, GOLDTEN, GOLDGUINEA and GOLDPETAL into cost-aware, walk-forward-validated relative-value intelligence. All performance is reported net of costs, computed on prices of contracts actually held, and split into gold-beta vs strategy alpha. "No persistent edge survives costs" is a first-class valid outcome.

## 2. Goals & metrics
| Goal | Metric |
|---|---|
| Correct normalization | Unit tests match hand-computed values for all four contracts; 0 tolerance beyond float error |
| No look-ahead | Future-shuffle invariance test passes; decisions at t use data <= t |
| Cost-aware | All headline P&L net; break-even cost multiple reported |
| Beta/alpha separation | Attribution identity holds (components sum to total); beta regression reported |
| Quiet alerts | **[NEW]** Alert rate on TEST reported (target <= 1 alert per ~10 trading days); "no signal" shown otherwise |
| Rigor | **[NEW]** Verdict produced mechanically by protocol (BACKTEST_PROTOCOL.md), incl. placebo and baseline tests |
| Reproducible | Same data + config => identical outputs; run manifest saved |

## 3. Priorities **[NEW]**
- **P0 (must ship):** data layer, normalization, calendar alignment, spreads/z-scores, cost model, walk-forward engine, attribution, stats battery + verdict, dashboard core, methodology, README/demo.
- **P1:** LAMS + alert cards, what-if cost simulator, edge-decay half-life, term structure/carry page.
- **P2 (only if time):** regime detection, basket constructor, Kalman residual, PGE index.

## 4. Contract mechanics
| Contract | Unit | Quoted per | Purity | Expiry window | Notes |
|---|---|---|---|---|---|
| GOLDM | 100 g | 10 g | 995 | 3rd-5th | |
| GOLDTEN | 10 g | 10 g | 999 | 27th-31st | listed 2025 onward |
| GOLDGUINEA | 8 g | 8 g | 999 | 27th-31st | |
| GOLDPETAL | 1 g | 1 g | 999 | 27th-31st | |
Lot size, tick size, margin, tender period are NOT in the problem statement: fill from MCX spec into `config/contracts.yaml` (no guessing). **[NEW]**

## 5. Data (see docs/DATA_CONTRACT.md)
Ingest, cache, validate returned Date vs requested; parse MM/DD/YYYY, 04SEP2026, padded symbols; key = (symbol, expiry_date); raw immutable, parsed to Parquet; DQ log; GOLDTEN short history handled. **[NEW]** Open questions to resolve from real files before coding: is Close = settlement; Volume/OI units; endpoint headers/rate limits; fallback plan (commit a small fixture set, cache full history locally, so demo never depends on MCX being reachable).

## 6. Analytics
### 6.1 Normalization **[FIXED]**
`pure_price_inr_per_g = close / quote_unit_g * (basis_purity / contract_purity)` with basis 999 (configurable; 999.9 in v1.0 rescaled everything by a constant 1.0009 and is unnecessary; ratios are basis-independent). Quote units per table above (GOLDM is quoted per 10 g although the trading unit is 100 g). P&L uses trading unit and lots, not the quote unit.
Note: a purity-adjusted price is not automatically "fair": deliverable purity and delivery terms create a structural spread. Spreads are therefore measured against their own rolling mean (residual), not assumed to be zero.

### 6.2 Expiry alignment and carry **[NEW, critical]**
GOLDM expires 3rd-5th; the other three expire 27th-31st, so same-named months differ by ~25-30 days and normalized prices embed different carry. Comparing them raw creates fake mispricings. Required:
1. For each trade date, list live contracts per family with days-to-expiry (DTE).
2. Pair contracts by nearest DTE, or interpolate each family's curve to a constant DTE grid (e.g. 30/60/90 d) using only same-day data.
3. Estimate implied carry (INR/g per day) from each family's own adjacent expiries and subtract carry x DTE gap before computing the spread.
4. Constant-maturity series are used for analytics only; trading and P&L use the real contracts by (symbol, expiry_date). No continuous near-month series.
Same-family calendar spreads (e.g. GOLDTEN Sep vs Oct) are also supported (term-structure module).

### 6.3 Relative-value signal
Spread s_t = adjusted pure_price(A) - adjusted pure_price(B), in INR/g. Views: absolute, rolling z-score (window in config), percentile rank; optional Kalman/OU residual (P2). Report OU half-life and ADF/stationarity check per pair on TRAIN. Entry/exit/stop/max-hold rules from config; thresholds calibrated on TRAIN only.

### 6.4 LAMS (P1) **[NEW: defined]**
`LAMS = liq_factor x (|dev_inr_per_g| - roundtrip_cost_inr_per_g - tender_penalty(DTE)) / sigma_spread`, where `liq_factor = min(1, thinnest_leg_capacity / target_size)`, capacity from volume AND open interest x participation cap. Alert only if |z| >= entry_z AND LAMS >= threshold (threshold set on TRAIN). Negative net edge => never alert.

### 6.5 Term structure & carry (P1)
Curve per family per day; roll-down (price change explained by DTE shrink under unchanged curve) separated from curve shape change; carry/roll yield uses actual calendar.

### 6.6 Lifecycle
Listing dates, liquidity development, DTE, tender windows. Forced exit N days before expiry/tender (config, verified). No entries inside buffer. Every entry/exit must fall inside the contract's live calendar.

### 6.7 PGE index (P2) **[FIXED]**
Liquidity-weighted synthetic reference of pure-gold price. Must be built on constant-maturity data and be leave-one-out when used to judge a contract (otherwise circular). Not required for the core spread signal.

## 7. Backtest & validation (see docs/BACKTEST_PROTOCOL.md) **[NEW: explicit]**
- Timeline: warmup, TRAIN, embargo, TEST touched once. Fill at t+1 settlement +/- slippage.
- Costs: brokerage, exchange, CTT, SEBI, stamp, GST, slippage in ticks with thin-day multiplier; per leg, entry and exit; sensitivity 0.5x-3x; break-even multiple.
- Sizing: gram-matched using real lots. 1 GOLDM = 10 GOLDTEN = 100 GOLDPETAL, but GOLDGUINEA (8 g) does not divide evenly, so rounding leaves residual gram exposure; it is reported and included in attribution. Position sized by thinnest leg and participation cap. Return on margin also reported.
- Battery: block-bootstrap CI, placebo/permutation, baselines (flat, random, naive), beta regression, parameter stability, multiple-testing adjustment.
- Verdict rule: edge claimed only if net alpha CI excludes zero on TEST at 1x costs, survives 2x at reduced level, and beats placebo; else "no persistent edge after costs".

## 8. Attribution **[FIXED: exact]**
Daily P&L = sum over legs of g_i x dP_i (g_i signed grams held, dP_i change in the contract's own price per gram).
- Beta = (sum g_i) x dRef, dRef = change of a reference gold price/index (net gram exposure x gold move).
- Alpha (RV) = sum g_i x (dP_i - dRef) (of which roll/carry component shown separately).
- Costs = fees + slippage. Residual = total - beta - alpha - costs (should be ~0; nonzero flags bugs or mark-price mismatch).
Waterfall chart + regression beta of daily strategy P&L on gold return.

## 9. Intelligence layer
Dashboard pages (P0): normalization table, RV heatmap, equity curve (net), attribution waterfall, verdict, methodology. P1: alert cards (cheap/rich legs, INR/g gap, percentile, net edge, basket grams, tender risk), what-if cost simulator, edge decay, term structure. Quiet by default. Exports: signals CSV, trade log CSV. Every number links to raw Bhavcopy rows.

## 10. Functional requirements
FR-1 Data ingestion/validation/registry. FR-2 Normalization with unit tests. FR-3 Calendar alignment + carry adjustment. FR-4 Signal generation (z, percentile; LAMS P1). FR-5 Walk-forward backtest + costs + sizing. FR-6 Attribution + stats battery + verdict. FR-7 Dashboard + alerts + exports. FR-8 Methodology card + assumption registry (visible in app). FR-9 Run manifest for reproducibility.

## 11. Non-functional
Full replay < 90 s on laptop (cached parquet); deterministic; offline-first (one-click "Replay"); every number traceable; runs locally or free tier; new contract added via config only; CI green (ruff + pytest).

## 12. Acceptance tests **[NEW]**
1. Normalization: 4 hand-computed cases pass. 2. Date mismatch/holiday discarded. 3. Future-shuffle invariance. 4. Determinism (hash of outputs equal across runs). 5. Cost monotonicity. 6. Attribution identity. 7. No pair compared across unadjusted expiry gap. 8. Contract inside exit buffer never traded. 9. GOLDTEN never used before listing. 10. Verdict logic unit-tested on synthetic edge (should detect) and pure noise (should reject).

## 13. Demo success criteria
Normalization table; heatmap; net equity curve on unseen TEST; attribution waterfall showing P&L is not gold beta; mechanical verdict statement ("X INR/g after costs" or "no persistent edge"); quiet alert view; cost what-if; methodology card readable in 60 s.

## 14. Milestones **[NEW]** (adjust to hackathon clock)
M1 data+normalization (T01-T04) -> M2 alignment+signals (T05-T07) -> M3 engine+costs+attribution (T08-T10) -> M4 stats+dashboard (T11-T12) -> M5 P1 features -> M6 README/demo/video (T17). Freeze features 3-4 h before submission.

## 15. Out of scope
Live execution, options, tick/L2 data, mobile, multi-metal, auth/compliance. Educational analytics only; not investment advice.

## 16. Risks
| Risk | Mitigation |
|---|---|
| MCX endpoint blocks/changes | cache raw data, commit small fixtures, fallback parquet |
| Thin GOLDPETAL/GUINEA | liquidity filters on volume+OI, participation cap, LAMS |
| Settlement != fill | slippage + stress costs |
| Cross-expiry carry mistaken for mispricing | 6.2 alignment |
| Overfitting/multiple tests | TRAIN-only calibration, frozen params, test once, adjustment |
| Unverified fees/lots/margins | config marked verify; results flagged until filled |
| AI-assisted code errors | AGENTS.md + rules, hand-computed tests, one task per chat |
| Judges non-quant | explainer cards, 60-second methodology |

## Appendix A - normalization worked example (hand-check)
GOLDM close 100,000 per 10 g, purity 995, basis 999: 100000/10 x 999/995 = 10,040.20 INR/g. GOLDPETAL close 10,050 per 1 g, purity 999: 10050/1 x 999/999 = 10,050 INR/g. (Illustrative arithmetic for the unit test, not market data.)

## Appendix D - changes from v1.0
1. Added expiry-date alignment and carry adjustment (largest gap: GOLDM vs others expire ~1 month apart). 2. Basis 999.9 -> 999, clarified structural purity premium. 3. PGE made constant-maturity and leave-one-out; demoted to P2. 4. Defined LAMS, attribution math, verdict rule. 5. Added lot/tick/margin config, lot rounding (GUINEA) and residual gram exposure. 6. Added TRAIN/embargo/TEST protocol, baselines, placebo, bootstrap, multiple-testing. 7. Added open data questions (Close vs settlement, units), endpoint fallback. 8. Added priorities, milestones, acceptance tests, deliverables. 9. Reduced reliance on Kalman/regime/basket (P2) to protect timeline.
