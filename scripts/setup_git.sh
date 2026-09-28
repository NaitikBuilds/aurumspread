#!/usr/bin/env bash
# Run once inside the repo. Sets LOCAL identity, hooks, and remote.
set -euo pipefail
git init -b main 2>/dev/null || true
git config user.name  "NaitikBuilds"
git config user.email "naitikchandel07@gmail.com"
git config core.hooksPath scripts/hooks
git config pull.rebase true
git config push.default current
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin git@github.com:NaitikBuilds/aurumspread.git
else
  git remote add origin git@github.com:NaitikBuilds/aurumspread.git
fi
chmod +x scripts/hooks/* scripts/*.sh
echo "Identity: $(git config user.name) <$(git config user.email)>"
echo "Remote:   $(git remote get-url origin)"
