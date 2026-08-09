# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""DRIFT.md LOUD-rule enforcement guard.

DRIFT.md is the canonical list of the drifts that keep coming back. Every rule marked **LOUD** is a
promise that `mio check` REFUSES the wrong form (exit 1) rather than letting it parse and drift past
silently. That promise is only real if something keeps testing it -- otherwise a future grammar or
validator change could quietly downgrade a LOUD rule to silent, and no one would notice until the
drift returned in the wild.

This guard runs the WRONG form of each LOUD rule through the real `mio check` path and asserts a
non-zero exit, and runs a paired RIGHT form where one exists and asserts exit 0. It is verification
only -- it changes no compiler behavior, it locks existing behavior.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: MOHIO_ENCRYPTION_KEY=testkey python3 tests/test_drift_loud_rules.py
"""
import os, sys, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
os.environ['PYTHONPATH'] = ROOT

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def check_exit(src):
    """Run `mio check` on src, return the real process exit code."""
    with tempfile.NamedTemporaryFile('w', suffix='.mho', dir='/tmp', delete=False) as fh:
        fh.write(src)
        path = fh.name
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'), 'check', path],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        return r.returncode
    finally:
        os.unlink(path)


def loud(label, src):
    """A LOUD rule: the WRONG form must be refused (non-zero exit)."""
    check(f"LOUD refuses: {label}", check_exit(src) != 0)

def valid(label, src):
    """The paired RIGHT form must pass (exit 0)."""
    check(f"valid passes: {label}", check_exit(src) == 0)


# 1. `if` never opens a block (trailing guard only)
loud("`if` as block opener", 'if x is more than 3\n    show "big"\nif: done\n')
valid("`if` as trailing guard", 'show "big" if x is more than 3\n')

# 4. `set` is retired
loud("retired `set`", 'set x to 5\n')

# 6. `as <type> <value>` trap; `number`/`num` retired as type names
loud("`hold x as int 5` (type before value)", 'hold x as int 5\n')

# 7. `done as NAME` is gone from every block
loud("`done as NAME` on closer",
     'check score\n    when score is above 5\n        show "hi"\ncheck: done as grade\n')
valid("naming on the action (`check score as grade`)",
      'check score as grade\n    when score is above 5\n        show "hi"\ncheck: done\n')

# 14. Route paths after `at` are unquoted
loud("quoted route path after `at`",
     'request for sh.C at "/c"\n    give back 200 "ok"\nrequest: done\n')

# 15. A not-built service fails at CHECK, not just RUN
loud("not-built service (`miosearch.index`)", 'miosearch.index "doc"\n')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
