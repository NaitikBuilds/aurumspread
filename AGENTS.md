# AGENTS.md — AurumSpread / AurumEdge (read this first, every session)

Project: cost-aware relative-value analytics on MCX gold futures (GOLDM, GOLDTEN, GOLDGUINEA, GOLDPETAL).
Hackathon: Hack in Hills '26, Problem Statement 03. Repo: NaitikBuilds/aurumspread.

## Source of truth (in priority order)
1. `docs/PRD.md` (what to build)  2. `config/contracts.yaml` (contract facts)  3. `docs/DATA_CONTRACT.md`
4. `docs/BACKTEST_PROTOCOL.md`  5. `docs/COST_MODEL.md`  6. `docs/ARCHITECTURE.md`  7. `docs/TASKS.md` (current work)
If code and docs disagree, STOP and ask; do not silently pick one.

## Hard rules
- Never invent MCX facts (lot sizes, tick sizes, fees, margins, expiry rules, column names). Read them from `config/contracts.yaml` or the real data. If a value is missing, add a `TODO(verify)` and ask the user.
- Never invent library APIs. Check installed versions (`pip show`, `help()`) or use the Context7 MCP before using an unfamiliar API.
- Contracts are keyed by (symbol, expiry_date). Never build a continuous near-month series for signals.
- No look-ahead: a decision on day t may only use data with date <= t. Fills use the rules in COST_MODEL.md.
- Never fabricate data, results, or performance numbers. Every number shown must trace to raw Bhavcopy rows.
- Small steps: one task from `docs/TASKS.md` at a time, with tests. Do not refactor unrelated files.
- Git: follow `.cursor/rules/60-git-commits.mdc`. Identity is fixed; never change git config.
