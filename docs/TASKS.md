# Task Board (give Cursor ONE task at a time; each ends with tests + a commit)
Prompt template: "Implement TASK-xx from docs/TASKS.md. Follow AGENTS.md. Plan first, then code, then run make check. Do not touch other modules."

| ID | Priority | Task | Done when |
|---|---|---|---|
| T01 | P0 | Config loaders (pydantic) for contracts/costs/backtest | invalid/missing values raise clear errors; tests |
| T02 | P0 | Bhavcopy fetch+cache+date validation | mismatched date discarded; raw saved untouched |
| T03 | P0 | Parser + validator + contract registry | key uniqueness, DQ log, fixtures from real rows |
| T04 | P0 | Normalization + unit tests | hand-computed values for all 4 contracts |
| T05 | P0 | Calendar/lifecycle (expiry, exit buffer, listing dates) | GOLDTEN pre-listing handled |
| T06 | P0 | Expiry-aligned pair construction + carry adjustment | no cross-expiry naive comparisons |
| T07 | P0 | Spread, rolling z-score (no look-ahead), percentile | future-shuffle invariance test |
| T08 | P0 | Cost model + liquidity filters + lot sizing | cost monotonicity test |
| T09 | P0 | Walk-forward engine + manifest | determinism test |
| T10 | P0 | Attribution (beta/alpha/cost/residual) | components sum to total |
| T11 | P0 | Stats battery (bootstrap, placebo, baselines, verdict) | verdict logic per protocol |
| T12 | P0 | Streamlit: normalization table, heatmap, equity, attribution, verdict, alerts, methodology | runs offline |
| T13 | P1 | LAMS + quiet-mode alert cards | alert rate reported |
| T14 | P1 | What-if cost simulator, edge-decay half-life | |
| T15 | P1 | Term-structure/carry page (roll-down vs curve change) | |
| T16 | P2 | Regime detection, basket constructor, Kalman residual | only if time |
| T17 | P0 | README, demo script, methodology card, 3-min video | judges can launch in 1 command |
