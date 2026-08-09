# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Workstream A / Unit A2 -- the _eval residual-Tree fallback fails loud.

A raw Lark Tree reaching the value evaluator means a grammar rule has no transformer method.
The old fallback evaluated the Tree's first child and returned it -- a QUIETLY WRONG value
(`(5 > 2)` -> 5, `s is.not "a"` -> "is s truthy"). A file-based re-measure across the full
suite + every example (subprocess-safe) found ZERO rules reaching it, so it was flipped to a
fail-loud raise that names the offending rule.

Run as a script: `python tests/test_residual_tree_fail_loud.py` (exit 0 = pass).
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

# ---- in-process: the fallback now raises instead of guessing ----
import mohio_interpreter as M
from lark import Tree, Token
interp = M.MohioInterpreter()
ctx = M.Context()

node = Tree('bogus_value_rule', [Token('NUMBER', '5')])   # old code returned 5 silently
try:
    r = interp._eval(node, ctx)
    _record("a residual Tree with a NUMBER child fails loud (was silently 5)", False,
            f"returned {r!r}")
except M.MohioRuntimeError as e:
    _record("a residual Tree with a NUMBER child fails loud (was silently 5)",
            "bogus_value_rule" in str(e))

try:
    interp._eval(Tree('empty_rule', []), ctx)
    _record("an empty residual Tree fails loud (was silently None)", False)
except M.MohioRuntimeError:
    _record("an empty residual Tree fails loud (was silently None)", True)

# A bare token is NOT a Tree, so the Tree fail-loud must not fire for it -- it still evaluates
# (via _eval's earlier isinstance(str) branch, since a lark Token subclasses str). The point is
# only that the flip's raise is scoped to Trees, not tokens.
_record("a bare token still evaluates, not caught by the Tree fail-loud",
        interp._eval(Token('NUMBER', '5'), ctx) is not None)

# ---- end to end: real expressions the fallback used to corrupt now compute correctly ----
def _run(src):
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode()); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, "run", path], cwd=REPO, env=ENV,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)

c1, o1 = _run('show (5 > 2)\n')   # the fallback used to make this 5; math_cmp makes it True
_record("`(5 > 2)` computes True, not the fallback's 5", c1 == 0 and "True" in o1, f"exit={c1}\n{o1[-160:]}")
c2, o2 = _run('show (2 > 5)\n')
_record("`(2 > 5)` computes False", c2 == 0 and "False" in o2, f"exit={c2}\n{o2[-160:]}")
c3, o3 = _run('score 10\nshow (score + 5)\n')
_record("a normal math expression still evaluates", c3 == 0 and "15" in o3, f"exit={c3}\n{o3[-160:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
