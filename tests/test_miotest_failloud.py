# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_miotest_failloud.py -- guards the miotest silent-no-op closure.

History: `miotest_block` / `miotest_unit` / `miotest_ai` were defined in the grammar and
referenced by NOTHING -- orphan rules (mohio.lark:1253-1258). `miotest "suite"` fell through
to a plain declaration and SILENTLY created a variable named miotest, doing nothing. Grammar
and transformer are now wired (MiotestDecl), but that alone only moves the silent no-op one
layer deeper: with zero interpreter references, the same construct could still silently do
nothing at RUN time instead of at parse time. This guards that it does not -- `miotest "suite"`
must fail LOUD through the real `mio run` execution path, by name, every sibling entry form.
"""
import os
os.environ['DATABASE_URL'] = ':memory:'
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

import mohio_data
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

def run(body):
    MohioInterpreter().run(transform(_P.parse(body), body), {})

PASS = 0; FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f"  ({detail})" if detail else ""))

def fails_loud_naming_miotest(body):
    """Must raise, and the message must name miotest specifically -- not just any
    generic 'no executor' fallback text, so a future refactor that swaps in a different
    unnamed catch-all still gets caught here."""
    try:
        run(body)
        return False, "ran clean -- SILENT NO-OP, the exact bug this guards against"
    except Exception as e:
        m = str(e).lower()
        if 'miotest' not in m:
            return False, f"raised but did not name miotest: {e}"
        if not any(s in m for s in ('not yet', 'not wired', 'not built', 'tracked')):
            return False, f"raised but does not read as a deferral: {e}"
        return True, ""

# All three grammar entry points (sibling sweep -- same shape, same orphan history).
CASES = {
    "miotest block (`miotest \"suite\"`)":
        'miotest "suite"\n    show "hi"\nmiotest: done\n',
    "miotest.unit":
        'miotest.unit "case"\n    show "hi"\nmiotest: done\n',
    "miotest.unit.ai":
        'miotest.unit.ai "generate cases for this"\nmiotest: done\n',
}

for label, body in CASES.items():
    ok, detail = fails_loud_naming_miotest(body)
    check(label, ok, detail)

# Regression guard for the ORIGINAL bug shape: even if the fail-loud message changed, the
# construct must never again resolve to a plain variable assignment. A run() that raises
# proves the block was actually dispatched as MiotestDecl, not silently declared and skipped.
check("miotest inside a larger program still fails loud (not swallowed by later statements)",
      fails_loud_naming_miotest('show "before"\nmiotest "suite"\n    show "hi"\nmiotest: done\nshow "after"\n')[0])

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
