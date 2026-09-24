#!/usr/bin/env bash
# Refresh the finance dashboard's data files, commit them and push.
#
#   finance/refresh.sh              # all parts
#   finance/refresh.sh stocks cpi   # only some parts (btc, stocks, etf, cpi)
#   finance/refresh.sh --dry-run    # fetch and show what would be committed, no commit or push
#
# Only finance/data/ is committed, so edits to the page itself are left for you to commit by hand.
set -uo pipefail

dry=0
if [[ ${1:-} == --dry-run ]]; then dry=1; shift; fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

branch=$(git symbolic-ref --short HEAD)
if [[ $branch != master && $branch != main ]]; then
  echo "on branch '$branch'; switch to master first" >&2
  exit 1
fi

uv run finance/fetch.py "$@"
fetch_status=$?

if [[ -z $(git status --porcelain -- finance/data) ]]; then
  echo "nothing changed under finance/data"
  exit $fetch_status
fi

git add finance/data
git status --short -- finance/data

if (( dry )); then
  echo "dry run: not committing"
  git reset -q -- finance/data
  exit $fetch_status
fi

git commit -q -m "finance data refresh $(date '+%Y-%m-%d %H:%M')"
git pull -q --rebase --autostash origin "$branch"
git push -q origin "$branch"
git log -1 --format='pushed %h  %s'

if (( fetch_status != 0 )); then
  echo "note: some parts failed to fetch (see above); the rest was pushed" >&2
fi
exit $fetch_status
