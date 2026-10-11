#!/usr/bin/env bash
# Refresh the front page's citation counts, commit them and push.
#
#   scholar/refresh.sh              # fetch, commit, push
#   scholar/refresh.sh --dry-run    # fetch and show what would be committed, no commit or push
#
# Only scholar/data/ is committed, so edits to the page itself are left for you to commit by hand.
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

uv run scholar/fetch.py
fetch_status=$?

if [[ -z $(git status --porcelain -- scholar/data) ]]; then
  echo "nothing changed under scholar/data"
  exit $fetch_status
fi

git add scholar/data
git status --short -- scholar/data

if (( dry )); then
  echo "dry run: not committing"
  git reset -q -- scholar/data
  exit $fetch_status
fi

git commit -q -m "scholar data refresh $(date '+%Y-%m-%d %H:%M')"
git pull -q --rebase --autostash origin "$branch"
git push -q origin "$branch"
git log -1 --format='pushed %h  %s'

if (( fetch_status != 0 )); then
  echo "note: some front-page papers were not found on the profile (see above); the rest was pushed" >&2
fi
exit $fetch_status
