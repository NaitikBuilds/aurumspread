# Setup checklist (do before opening Cursor)

## 1. Accounts / auth
- Create empty repo `NaitikBuilds/aurumspread` on GitHub (no README, so first push is clean).
- SSH: `ssh-keygen -t ed25519 -C "naitikchandel07@gmail.com"`, add the public key in GitHub > Settings > SSH keys, test `ssh -T git@github.com`.
- Add naitikchandel07@gmail.com as a verified email in GitHub > Settings > Emails (or use your noreply address instead if "Block command line pushes that expose my email" is on, then update `scripts/setup_git.sh` and `scripts/hooks/pre-commit`).
- Create a fine-grained GitHub PAT (repo: aurumspread only; contents + pull requests + issues rw) and export it: `export GITHUB_PAT=...` (never commit it).

## 2. Local
```
cd aurumspread && python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && bash scripts/setup_git.sh
git add -A && git commit -m "chore: initial project scaffold" && git push -u origin main
```
Confirm: `git log -1 --format='%an <%ae>'` prints NaitikBuilds <naitikchandel07@gmail.com>.

## 3. Cursor
- Open the folder in Cursor. Settings > Rules: confirm 00, 10, 60 show as "Always". 
- Settings > MCP: enable `context7`, `github`, `fetch` (max 3; more MCPs = worse tool selection).
- Install recommended extensions when prompted (.vscode/extensions.json).
- In Settings check any commit-message/attribution or "co-author" option is OFF (the commit-msg hook will also reject such trailers).
- Use Agent mode; give ONE task from docs/TASKS.md per chat; start a new chat per task; @-mention `AGENTS.md`, the relevant doc, and the target files.
- Turn on "Ask before running terminal commands" for git commands.

## 4. Before coding (human-only, 30-45 min)
1. Download 2-3 real Bhavcopy files, save under `tests/fixtures/`; resolve every item in DATA_CONTRACT.md "Open questions".
2. Fill `verify` fields in `config/contracts.yaml` and `config/costs.yaml` from MCX pages and your broker's contract note.
3. Run a data audit (row counts per contract, first/last dates, GOLDTEN start) and freeze the train/test dates in `config/backtest.yaml` + DECISIONS.md.
