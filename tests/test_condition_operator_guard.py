# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Workstream A / Unit A1 -- the condition operator dispatcher fails loud on an
operator it does not implement, instead of silently returning False.

The reachable case: the grammar's MATH_CMP_OP accepts a bare `=` (single equals) that
CMP_OP does not, and `math_cmp` passed it through unnormalized. `_eval_condition` handled
`==` but not `=`, so `(x = y)` fell to a catch-all `return False` and was ALWAYS false
regardless of the values -- a silent-wrong-answer. It now fails loud and names the operator.

Run as a script: `python tests/test_condition_operator_guard.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

_p = _f = 0

def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def _run(src):
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, "run", path], cwd=REPO, env=ENV,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)

# Adversarial: single `=` fails loud for BOTH a would-be-true and a would-be-false case,
# proving it is no longer a value-independent silent False.
c1, o1 = _run('score 100\nshow (score = 100)\n')   # would-be TRUE
_record("`(score = 100)` fails loud, names the operator (was silent False)",
        c1 != 0 and "`=` is not a comparison operator" in o1, f"exit={c1}\n{o1[-200:]}")

c2, o2 = _run('score 50\nshow (score = 100)\n')     # would-be FALSE
_record("`(score = 100)` fails loud on a false case too (not value-dependent)",
        c2 != 0 and "`=` is not a comparison operator" in o2, f"exit={c2}\n{o2[-200:]}")

# Controls: the handled operators still work and do NOT raise.
c3, o3 = _run('score 100\nshow (score == 100)\n')
_record("`==` still evaluates to True", c3 == 0 and "True" in o3, f"exit={c3}\n{o3[-200:]}")

c4, o4 = _run('score 50\nshow (score == 100)\n')
_record("`==` still evaluates to False", c4 == 0 and "False" in o4, f"exit={c4}\n{o4[-200:]}")

c5, o5 = _run('score 100\ncheck score\n    when score is above 50\n        show "big"\n'
              '    otherwise\n        show "small"\ncheck: done\n')
_record("`is above` still works", c5 == 0 and "big" in o5, f"exit={c5}\n{o5[-200:]}")

c6, o6 = _run('name "Bo"\ncheck name\n    when name is "Bo"\n        show "hi Bo"\ncheck: done\n')
_record("`is <string>` still works", c6 == 0 and "hi Bo" in o6, f"exit={c6}\n{o6[-200:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
