#!/usr/bin/env bash
# Counts commits and lines by the Agent: trailer, for the README (DESIGN.md §16).
set -euo pipefail

for agent in "muse-code"; do
  commits=$(git log --no-merges --format='%H%x09%(trailers:key=Agent,valueonly,separator=%x20)' | awk -F'\t' -v a="$agent" 'index($2, a) == 1 { print $1 }')
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
    done < <(git show --numstat --format= "$sha")
  done
  echo "$agent: $count commits, +$added -$removed lines (excluding data and lockfiles)"
done
