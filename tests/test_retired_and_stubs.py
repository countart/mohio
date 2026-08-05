# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_retired_and_stubs.py -- guards the Tier-0 silent-no-op class-closure (A-items)
and the A5/A6 retirements.

- _stub is the single helper that all not-yet-built verbs (copy, sign, apply,
  compare, summarize, calculate, join, modify, check-mioql, rerun, validate-legacy)
  routed through. It used to silently return None. Now it fails loud, closing the
  whole class at the source.
- A5: `request outbound` is retired -> fail loud, steering to miohttp.
- A6: block-`if` (IfBlock node/transformer/executor) removed (No-If canon).
"""
import os
os.environ['DATABASE_URL'] = ':memory:'
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_H = 'connect db as sqlite from env.DATABASE_URL\n'

def run(body):
    MohioInterpreter().run(transform(_P.parse(_H + body), _H + body), {})

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL: {name}")

# --- Tier-0: _stub fails loud at the source (proves the whole class) ---
try:
    MohioInterpreter()._stub('demo verb', None, None)
    check("_stub fails loud (class-closure)", False)
except Exception as e:
    check("_stub fails loud (class-closure)", 'silently' in str(e).lower() or 'not yet' in str(e).lower())

# --- a stub-routed verb fails loud end-to-end ---
def fails_loud(body):
    try:
        run(body); return False
    except Exception as e:
        m = str(e).lower(); return 'silently' in m or 'not yet' in m or 'retired' in m
check("copy fails loud", fails_loud('copy "a.txt" to "b.txt"\n'))

# --- a REAL verb that must keep working (validate routes to the real executor) ---
def runs_clean(body):
    try:
        run(body); return True
    except Exception:
        return False
check("validate still works (not swept)", runs_clean('validate using email_rules\n'))

# --- A5: request outbound is retired and steers to miohttp ---
try:
    run('request job from queue.tasks\nrequest: done\n')
    check("request outbound retired", False)
except Exception as e:
    check("request outbound retired (steers to miohttp)", 'miohttp' in str(e).lower())

# --- A6: the IfBlock node is gone ---
try:
    from mohio_ast import IfBlock  # noqa
    check("IfBlock node removed", False)
except ImportError:
    check("IfBlock node removed", True)

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
