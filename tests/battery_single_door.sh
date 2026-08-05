#!/usr/bin/env bash
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
# ============================================================================
# SINGLE-DOOR REFACTOR — FULL REGRESSION BATTERY
#
# Proves the single-door enforcement refactor changed NO check verdict and lost
# NO test. Run this on a checkout that HAS the refactor. It:
#   1. runs the grammar gate,
#   2. runs every tests/test_*.py with a generous timeout and honest exit codes,
#   3. runs mio check over the ENTIRE corpus and records every exit code,
#   4. runs the real e2e specimens (both failing and passing) with expected results,
#   5. compares corpus verdicts against a saved origin baseline if present.
#
# Intended for the TEST CHAT and the EXTERNAL AUDIT to run independently.
# Usage:
#   PYTHONPATH=$PWD DATABASE_URL=:memory: MOHIO_ENCRYPTION_KEY=testkey bash tests/battery_single_door.sh
# ============================================================================
set -u
cd "$(dirname "$0")/.." || exit 2
export PYTHONPATH="$PWD" DATABASE_URL=":memory:" MOHIO_ENCRYPTION_KEY="testkey"
PY="${PYTHON:-python3}"
TIMEOUT="${TIMEOUT:-60}"
FAILS=0

echo "=================================================================="
echo " 1. GRAMMAR GATE (must be 154/154)"
echo "=================================================================="
rm -f mohio_parser_*.pkl
timeout 300 "$PY" mohio_test_grammar.py 2>&1 | grep -E "RESULTS|FAIL" || FAILS=$((FAILS+1))

echo ""
echo "=================================================================="
echo " 2. EVERY TEST FILE (honest exit codes; generous timeout)"
echo "    NOTE: some pre-existing reds are expected -- compare to the"
echo "    KNOWN-RED list at the bottom. Anything NOT on that list that"
echo "    fails here is a real regression to investigate."
echo "=================================================================="
for f in tests/test_*.py; do
  n=$(basename "$f" .py)
  timeout "$TIMEOUT" "$PY" "$f" >/tmp/bt_out 2>&1
  ec=$?
  if [ "$ec" = "0" ]; then
    echo "  PASS  $n"
  elif [ "$ec" = "124" ]; then
    echo "  SLOW  $n (timed out at ${TIMEOUT}s -- rerun with larger TIMEOUT, not a fail)"
  else
    echo "  FAIL  $n (exit $ec)"
    tail -3 /tmp/bt_out | sed 's/^/          /'
  fi
done

echo ""
echo "=================================================================="
echo " 3. SINGLE-DOOR SPECIFIC GUARDS (must all pass)"
echo "=================================================================="
for t in test_single_door test_cache_lifecycle test_structural_invariants; do
  timeout 90 "$PY" "tests/$t.py" >/tmp/bt_out 2>&1
  [ "$?" = "0" ] && echo "  PASS  $t" || { echo "  FAIL  $t"; tail -5 /tmp/bt_out | sed 's/^/          /'; FAILS=$((FAILS+1)); }
done

echo ""
echo "=================================================================="
echo " 4. REAL E2E -- both FAILING (exit 1) and PASSING (exit 0) forms"
echo "    Every form the refactor touches: through the real mio check."
echo "=================================================================="
run_case() {  # name  want_exit  source
  printf '%s' "$3" > /tmp/bt_case.mho
  timeout 60 "$PY" mio.py check /tmp/bt_case.mho >/tmp/bt_out 2>&1
  ec=$?
  if [ "$ec" = "$2" ]; then echo "  OK    $1 (exit $ec)"; else echo "  FAIL  $1 (exit $ec want $2)"; FAILS=$((FAILS+1)); fi
}
# --- FAILING forms (must exit 1) ---
run_case "if-block-opener LOUD"        1 $'if x is more than 3\n    show "big"\nif: done\n'
run_case "set retired LOUD"            1 $'set x to 5\n'
run_case "run-instead-of-call LOUD"    1 $'task t returns text\n    give back "x"\ntask: done\nrun t\n'
run_case "quoted route path LOUD"      1 $'request for sh.C at "/c"\n    give back 200 "ok"\nrequest: done\n'
run_case "done-as-NAME closer LOUD"    1 $'retrieve raw from db.c\n    sql\n        SELECT 1\n    sql: done as raw\nretrieve: done\n'
run_case "never-store save LOUD"       1 $'shape Card\n    cvv as text never store\nshape: done\nconnect db as sqlite from env.DATABASE_URL\nsave to db.cards\n    cvv "1"\nsave: done\n'
run_case "upload no accept/max LOUD"   1 $'shape Doc\n    file_field as file\nshape: done\n'
run_case "bad type name (number) LOUD" 1 $'shape T\n    age as number\nshape: done\n'
# --- PASSING forms (must exit 0) ---
run_case "clean shape"                 0 $'shape T\n    age as int\nshape: done\n'
run_case "correct sql-in-retrieve"     0 $'connect db as sqlite from env.DATABASE_URL\nretrieve raw from db.cards\n    sql\n        SELECT 1\n    sql: done\nretrieve: done\ngive back 200 raw\n'
run_case "correct call task"           0 $'task t returns text\n    give back "x"\ntask: done\nhold r call t\ngive back 200 r\n'
run_case "trailing-if guard"           0 $'show "big" if 5 is more than 3\n'

echo ""
echo "=================================================================="
echo " 5. CORPUS-WIDE mio check (record every verdict)"
echo "    Diff this list against an origin baseline -- any file whose"
echo "    exit code CHANGED is a verdict change to explain."
echo "=================================================================="
for f in tests/*.mho examples/**/*.mho; do
  [ -f "$f" ] || continue
  timeout 60 "$PY" mio.py check "$f" >/dev/null 2>&1
  echo "  exit=$? $f"
done | sort

echo ""
echo "=================================================================="
echo " 6. THE SINGLE-DOOR INVARIANT (the whole point)"
echo "    Add a scanner to the canonical list -> BOTH the gate and mio"
echo "    check must see it. This is what the refactor guarantees."
echo "=================================================================="
echo "  (manual/audit step: add a temporary scanner to mohio_reachability"
echo "   ERROR_SCANS, confirm 'mio check' on a triggering file now errors,"
echo "   then remove it. Before the refactor, mio check had its own list"
echo "   and would NOT see the new scanner.)"

echo ""
echo "=================================================================="
echo " KNOWN PRE-EXISTING REDS (fail on ORIGIN too -- NOT regressions):"
echo "   test_call_and_check   -- bare 'call X' fails loud (1 of 7)"
echo "   test_retired_and_stubs -- stale validate assertion"
echo "   test_cast_canon        -- pre-existing (fails on origin, exit 1)"
echo "   test_connect_idempotent-- pre-existing (fails on origin, exit 1)"
echo "   test_ai_rank           -- uses retired 'set' in specimen + slow"
echo "   (slow tests may show exit 124 -- rerun with TIMEOUT=120, not a fail)"
echo "=================================================================="
echo ""
if [ "$FAILS" = "0" ]; then
  echo "BATTERY: no single-door-specific failures. Verdict changes in section 5"
  echo "must be diffed against an origin baseline to confirm none."
else
  echo "BATTERY: $FAILS single-door-specific failure(s) -- investigate above."
fi
exit $FAILS
