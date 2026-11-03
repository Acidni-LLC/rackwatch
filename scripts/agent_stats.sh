#!/usr/bin/env bash
# Counts commits and lines by the Agent: line in each commit message, for the README (DESIGN.md §16).
set -euo pipefail

for agent in "muse-code"; do
  commits=$(git log --no-merges --format=%H | while read -r sha; do
    if git log -1 --format=%B "$sha" | grep -q "^Agent: $agent"; then echo "$sha"; fi
  done)
  count=$(printf '%s\n' "$commits" | sed '/^$/d' | wc -l | tr -d ' ')
  added=0
  removed=0
  for sha in $commits; do
    while read -r plus minus path; do
      case "$path" in
        data/*|*package-lock.json|docs/preflight.json|docs/eval.json) continue ;;
      esac
      [ "$plus" = "-" ] && continue
      added=$((added + plus))
      removed=$((removed + minus))
    done < <(git show --numstat --ignore-cr-at-eol --format= "$sha")
  done
  echo "$agent: $count commits, +$added -$removed lines (excluding data and lockfiles)"
done
