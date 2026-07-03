#!/bin/zsh
# Rebuild decks for songs not yet perfect. Phase 1: existing wavs (silent).
# Phase 2: record Know + Freedom (plays audio).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

phase1=(
  "Who You Say I Am (Live)"
  "First Love (Live)"
  "Holy Ground (feat. Melodie Malone) [Live]"
  "One (Live At Church)"
)
phase2=(
  "Know (Be Still) [Live in Anaheim, California]"
  "Freedom (feat. Kim Walker-Smith) [Live]"
)

for t in "${phase1[@]}" "${phase2[@]}"; do
  echo "=== BUILD: $t ==="
  $PY -m tests.harness build "$t" 2>&1 | tail -4
  echo "=== DONE: $t (exit $?) ==="
done
echo "ALL BUILDS DONE"
