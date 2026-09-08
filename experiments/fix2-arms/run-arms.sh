#!/usr/bin/env bash
# Fix-2 reproduction: three SYSTEM-prompt arms, four cases, three reps each.
#
# Retrieval is constant throughout (same corpus, same .chroma, same k), so every
# verdict change is prompt-attributable. Same method report.md used for the
# gold-025 / gold-022 flips.
#
# Usage:  bash experiments/fix2-arms/run-arms.sh
# Needs:  .chroma built (rag.py ingest) and a working key in .env.
# Cost:   3 arms x 4 cases x 3 reps = 72 model calls.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
cd "$REPO"

CASES="gold-007 gold-024 gold-029 gold-030"
PY="${PY:-.venv/bin/python}"

live_arm () {
  grep -q "NOT an out-of-scope topic" rag.py && echo "C (post-Fix-2)" \
    || { grep -q "Scope: you answer questions" rag.py && echo "B (attempt-1)" \
         || echo "A (pre-Fix-2)"; }
}

run_arm () {
  local want="$1" patch="${2:-}"
  git checkout -- rag.py
  [[ -n "$patch" ]] && git apply "$HERE/$patch"
  local got; got="$(live_arm)"
  echo; echo "═════════ ARM $want · live: $got ═════════"
  [[ "$got" == "$want"* ]] || { echo "ARM MISMATCH — aborting"; exit 1; }
  for rep in 1 2 3; do
    echo "── rep $rep"
    RAG_TRACE_FILE="$HERE/trace-arm-$want-rep$rep.jsonl" \
      $PY rag.py eval --case $CASES 2>&1 | grep -E "gold-0|retrieval [0-9]+/"
  done
  git checkout -- rag.py
}

run_arm A arm-A.patch
run_arm B arm-B.patch
run_arm C
echo; echo "traces: $HERE/trace-arm-*-rep*.jsonl  (gitignored)"
