<div align="center">

# AurumSpread

### Four gold contracts. One metal. Is anyone mispricing it, after costs?

**Cost-aware, walk-forward-validated relative-value analytics on MCX gold futures**
GOLDM · GOLDTEN · GOLDGUINEA · GOLDPETAL

![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![ruff](https://img.shields.io/badge/lint-ruff-261230)
![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)
![Hack in Hills '26](https://img.shields.io/badge/Hack%20in%20Hills%20'26-PS--03-D4AF37)

*Product name: **AurumEdge** · Repo: `NaitikBuilds/aurumspread` · Hack in Hills '26, Problem Statement 03: Commodity Derivatives Intelligence*

</div>

---

## The trap this project exists to avoid

MCX lists four futures on the same underlying. Put their closing prices side by side and you will see "mispricings" everywhere. Almost all of them are fake. Three things conspire:

| Illusion | Why it happens | What AurumSpread does |
|---|---|---|
| **Different numbers, same gold** | GOLDM is quoted per **10 g** at **995** purity; the other three are quoted per 8 g / 10 g / 1 g at **999** purity. Raw closes are not comparable. | Normalise every contract to **INR per gram of pure gold** at one basis purity (`core/normalize.py`). |
| **Same month, different date** | GOLDM expires on the **3rd–5th**; GOLDTEN, GOLDGUINEA and GOLDPETAL expire on the **27th–31st**. A "September vs September" comparison silently spans ~25–30 days of carry. | Pair contracts by **nearest days-to-expiry**, estimate each family's implied carry from **its own curve**, and shift the far leg before measuring the spread. If no carry estimate exists, the spread is `NaN`, never the naive gap (`core/carry.py`). |
| **Edge that evaporates at the broker** | Thin contracts, multi-leg round trips, CTT, GST, slippage. A 5 INR/g gap can be a loss. | Every P&L figure is **net of costs**, sized on the **thinnest leg**, stress-tested at 0.5×–3× costs, and split into **gold beta vs. strategy alpha**. |

The product is designed so that **"no persistent edge survives costs" is a first-class, valid answer**. The verdict is produced mechanically by a frozen protocol, not by whoever is presenting.

---

## The pipeline

```
 MCX Bhavcopy CSV ──► fetch + cache ──► parse + validate ──► contract registry
   (raw, immutable)   (date-checked)    (DQ log, keys)      (symbol, expiry_date)
                                                                     │
                                                                     ▼
              spreads / z-scores ◄── expiry alignment + carry ◄── normalise to INR/g
              (rolling, no look-ahead)  (nearest-DTE pairing)     (purity + quote unit)
                        │
                        ▼
      walk-forward engine ──► attribution ──► stats battery ──► verdict ──► Streamlit
      (t+1 fills, costs)     (beta/alpha/     (bootstrap, placebo,   ("X INR/g net"
                              cost/residual)   baselines)             or "no edge")
```

Every contract is keyed by **(symbol, expiry_date)**. There is no continuous "near-month" series anywhere in the signal path, so there are no roll jumps to mistake for opportunities.

---

## Normalisation, by hand

The whole edifice rests on one formula (PRD §6.1), so it is the first thing tested against hand-computed values:

```
pure_price_inr_per_g = close_inr / quote_unit_g × (basis_purity / contract_purity)
```

Illustrative arithmetic (not market data, from PRD Appendix A):

| Contract | Quoted close | Quote unit | Purity | INR / g of 999 gold |
|---|---:|---:|---:|---:|
| GOLDM | 100,000 | 10 g | 995 | 100000 / 10 × 999 / 995 = **10,040.20** |
| GOLDPETAL | 10,050 | 1 g | 999 | 10050 / 1 × 999 / 999 = **10,050.00** |

A purity-adjusted price is still **not** a "fair" price: deliverable purity and delivery terms create a structural premium. So spreads are always measured against their **own rolling mean**, never against zero.

---

## The honesty contract

These are not aspirations; they are enforced by tests, config validation and the docs in `docs/`.

- **No look-ahead.** A decision on day *t* uses only data with `trade_date <= t`. Signals form at close *t*, fill at settlement *t+1* ± slippage. A future-shuffle invariance test guards this.
- **No invented MCX facts.** Lot sizes, tick sizes, fees, margins and tender periods are **not** in the problem statement. They live in `config/*.yaml` as `verify:true` fields, are `null` until a human fills them from MCX/broker sources, and the code surfaces them via `unverified_fields()` so results can be flagged.
- **Raw data is immutable.** Bhavcopy responses are written byte-for-byte to `data/raw/` and never overwritten. A response whose `Date` doesn't match the request (holiday substitution, malformed payload) is quarantined under `data/raw/rejected/` and logged, not silently accepted.
- **Every number traces to a row.** Parsed frames carry `source` and `source_row` back to the raw file and line.
- **Deterministic.** Same data + same config ⇒ identical outputs. Seed lives in config.
- **Costs first.** Round-trip cost per gram is computed *before* entry and fed into the alert score; negative net edge never alerts.
- **Verdict by protocol.** Edge is claimed only if the net-alpha CI excludes zero on an untouched TEST window at 1× costs, survives 2× at a reduced level, and beats a placebo. Otherwise: *"no persistent edge after costs."*

---

## Contract universe

From the problem statement (see `config/contracts.yaml`; blank fields are deliberately unverified):

| Contract | Trading unit | Quoted per | Purity | Expiry window | Note |
|---|---:|---:|---:|---|---|
| GOLDM | 100 g | 10 g | 995 | 3rd–5th | the odd one out on both purity and calendar |
| GOLDTEN | 10 g | 10 g | 999 | 27th–31st | listed 2025 onward; shorter history handled explicitly |
| GOLDGUINEA | 8 g | 8 g | 999 | 27th–31st | 8 g does not divide evenly into gram-matched baskets; residual exposure is reported |
| GOLDPETAL | 1 g | 1 g | 999 | 27th–31st | thinnest leg most days |

---

## What's built, what's next

Progress tracks `docs/TASKS.md` one task per commit, each landing with tests.

| Layer | Status | Modules | Guarantees |
|---|---|---|---|
| Config loaders | ✅ done | `config.py` | Frozen pydantic models, `extra="forbid"`, unverified fields exposed |
| Data layer | ✅ done | `data/fetch.py` `parse.py` `validate.py` `registry.py` `dq.py` `cli.py` | Date-checked cache, DQ event log, unique (symbol, expiry) keys, parquet artefacts |
| Normalisation | ✅ done | `core/normalize.py` | Hand-computed unit tests for all four contracts |
| Calendar & lifecycle | ✅ done | `core/calendar.py` `lifecycle.py` | `pre_listing / live / exit_buffer / expired`; GOLDTEN never used before listing |
| Expiry alignment & carry | ✅ done | `core/carry.py` | Nearest-DTE pairing, own-curve carry, constant-maturity grid (analytics only) |
| Term structure | ✅ done | `core/term_structure.py` | Calendar spreads, roll-down vs curve-change decomposition (sums exactly) |
| Hand-off | ✅ done | `core/prepare.py` | One call: registry ⇒ normalised, lifecycle-annotated, pair-spread frames |
| Signals (z-score, percentile, LAMS) | ⏳ next | `signals/` | Future-shuffle invariance test |
| Walk-forward engine + costs + sizing | ⏳ planned | `backtest/` | Determinism, cost monotonicity, attribution identity |
| Stats battery + verdict | ⏳ planned | `backtest/stats.py` | Bootstrap, placebo, baselines, multiple-testing note |
| Streamlit dashboard | ⏳ planned | `app/` | Offline "Replay", quiet-by-default alerts, methodology card |

Current state: `make check` (ruff + pytest) is green with **218 tests** on `main`. No backtest results exist yet, and none will appear here until they are computed by code in this repo on real Bhavcopy rows.

---

## Quickstart

Requires Python 3.11.

```bash
git clone https://github.com/NaitikBuilds/aurumspread.git
cd aurumspread
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
make check                         # ruff + pytest
```

Data (needs `config/data_source.yaml` filled with the verified endpoint; fetch refuses to run while it is `null`):

```bash
make fetch START=2025-01-01 END=2026-09-26   # raw CSVs -> data/raw/, date-validated
make ingest                                  # raw -> data/processed/*.parquet + audit summary
```

`make ingest` prints per-symbol row counts, first/last trade dates and data-quality events, which is the audit used to freeze TRAIN/TEST dates in `docs/DECISIONS.md` **before** any result is looked at.

Programmatic use of what exists today:

```python
from aurumspread.config import load_all
from aurumspread.core.prepare import pair_spreads, prepare_prices
from aurumspread.data.registry import ContractRegistry

cfg = load_all()  # config/*.yaml, cross-checked
registry = ContractRegistry.load(cfg.data_source.processed_dir_abs)
prepared = prepare_prices(registry, cfg)  # + pure_price_inr_per_g, dte_days, status
spreads = pair_spreads(prepared, "GOLDM", "GOLDTEN", cfg)  # carry-adjusted, INR/g
```

---

## Repository map

```
aurumspread/
├── config/            contracts.yaml  costs.yaml  backtest.yaml  data_source.yaml
│                      every tunable number lives here; nothing is hard-coded in logic
├── docs/
│   ├── PRD.md               what to build (source of truth #1)
│   ├── DATA_CONTRACT.md     Bhavcopy fields, parsing rules, validation, open questions
│   ├── BACKTEST_PROTOCOL.md TRAIN / embargo / TEST, validation battery, verdict rule
│   ├── COST_MODEL.md        fills, slippage, fees, sensitivity
│   ├── ARCHITECTURE.md      module layout and data flow
│   ├── DECISIONS.md         append-only decision log
│   └── TASKS.md             the task board
├── src/aurumspread/
│   ├── config.py      typed loaders
│   ├── data/          fetch · parse · validate · registry · dq · cli
│   └── core/          normalize · calendar · lifecycle · carry · term_structure · prepare
├── tests/             unit + invariance tests (fixtures from real Bhavcopy rows)
└── AGENTS.md          rules every contributor, human or AI, reads first
```

Conventions: money in INR, quantities in grams, and column names carry their units (`pure_price_inr_per_g`, `carry_inr_per_g_per_day`, `dte_days`). Pure functions, typed, config-driven, offline-capable.

---

## Why judges should care

Most relative-value demos show a heatmap of raw price gaps and a rising equity curve. AurumSpread shows the same heatmap **after** purity, quote-unit and expiry-carry adjustment, then asks whether anything left over survives realistic costs on data the strategy has never seen, and reports the answer either way. The deliverable is not a number; it is a **method that cannot be flattered**.

---

## Documentation

Start with `AGENTS.md`, then read in priority order: `docs/PRD.md` → `config/contracts.yaml` → `docs/DATA_CONTRACT.md` → `docs/BACKTEST_PROTOCOL.md` → `docs/COST_MODEL.md` → `docs/ARCHITECTURE.md` → `docs/TASKS.md`. If code and docs disagree, the disagreement is a bug to raise, not a choice to make silently.

---

<div align="center">

Educational analytics only. Not investment advice. No live execution.

</div>
