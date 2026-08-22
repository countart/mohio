# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): run_scans()'s per-scanner `except Exception: pass`
used to drop a crashing scanner's ENTIRE contribution to `mio check` with zero trace -- the
check still reported clean for that program, and nobody could tell a whole diagnostic
category silently didn't run. Fixed: still never takes down the check itself (the invariant
in the docstring is unchanged -- a scanner reports or says nothing, it never crashes `mio
check`), but a crashing scanner now prints a named warning to stderr instead of vanishing.

Run: `python tests/test_scan_runner_resilience.py`.
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

import mohio_reachability as mr

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class _FakeProgram:
    pass


def _boom(program):
    raise RuntimeError("simulated scanner bug")
_boom.__name__ = 'scan_typos'


# A crashing WARNING_SCANS entry: run_scans must not crash, and must name the scanner.
orig_warning_scans = mr.WARNING_SCANS
mr.WARNING_SCANS = tuple(_boom if s is mr.scan_typos else s for s in orig_warning_scans)
buf = io.StringIO()
try:
    with contextlib.redirect_stderr(buf):
        errors, warnings = mr.run_scans(_FakeProgram())
    crashed = False
except Exception:
    crashed = True
finally:
    mr.WARNING_SCANS = orig_warning_scans

check("run_scans() does not crash when a WARNING scanner raises", not crashed)
out = buf.getvalue()
check("a warning names the failing scanner", 'scan_typos' in out, out)
check("a warning names the exception type", 'RuntimeError' in out, out)

# A crashing ERROR_SCANS entry: same guarantee.
orig_error_scans = mr.ERROR_SCANS
first_error_scan = orig_error_scans[0]
_boom2 = lambda program: (_ for _ in ()).throw(RuntimeError("simulated error-scan bug"))
_boom2.__name__ = getattr(first_error_scan, '__name__', 'unknown_error_scan')
mr.ERROR_SCANS = (_boom2,) + orig_error_scans[1:]
buf2 = io.StringIO()
try:
    with contextlib.redirect_stderr(buf2):
        errors2, warnings2 = mr.run_scans(_FakeProgram())
    crashed2 = False
except Exception:
    crashed2 = True
finally:
    mr.ERROR_SCANS = orig_error_scans

check("run_scans() does not crash when an ERROR scanner raises", not crashed2)
check("the failing error scanner's crash is visible on stderr",
      'RuntimeError' in buf2.getvalue(), buf2.getvalue())

# Regression: with nothing patched, a real program's real scanners still produce correct
# results (no leftover state from the monkeypatches above).
import mohio_data
from lark import Lark
from mohio_transformer_ast import transform

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
src = 'sector: demo-regulated\n'
prog = transform(P.parse(src), src)
errors3, warnings3 = mr.run_scans(prog)
check("regression: a real program's real scanners still run cleanly after the monkeypatches",
      any('append_only' in w.message for w in warnings3), [w.message for w in warnings3])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
