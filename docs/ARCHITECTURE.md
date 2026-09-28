# Architecture
```
src/aurumspread/
  data/       fetch.py parse.py validate.py registry.py dq.py cli.py  (raw -> parquet, registry)
  core/       normalize.py calendar.py lifecycle.py carry.py prepare.py pge.py
  signals/    spread.py zscore.py lams.py regime.py
  backtest/   engine.py costs.py sizing.py attribution.py stats.py run.py
  app/        main.py pages/ (heatmap, curves, backtest, alerts, whatif, methodology)
  config.py   typed loaders (pydantic) for config/*.yaml (contracts, costs, backtest, data_source)
tests/        fixtures/ (real Bhavcopy rows), unit + invariance tests
outputs/      run manifests, trade logs, equity curves (git-ignored)
```
Flow: raw CSV -> parse/validate -> contract registry -> normalize (INR/g) -> calendar+carry alignment -> spreads/z -> LAMS -> walk-forward engine (+costs) -> attribution/stats -> parquet artifacts -> Streamlit (read-only).
Design rules: pure functions, typed dataclasses, config-driven, deterministic, offline-capable.
