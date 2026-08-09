# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Workstream A / Unit A3 -- an undefined bare variable used as a VALUE fails loud
instead of silently resolving to None, in the three positions where that silence bites:

  * save field   -- `save to db.t / n nobody` silently WROTE None (live data corruption)
  * hold source  -- `hold x nobody` silently bound None
  * task argument -- `call greet with nobody` silently passed None

It reuses the same unknown_variable rule `show` and interpolation already enforce, and ONLY
catches a lone undefined name. The load-bearing exemptions are proven too: a defined value
(even empty), a dotted field access that is a real key holding None (legitimately null,
not missing), a `default`, and a `when empty` check on an undefined subject all keep working --
ctx.get and its None are untouched.

T0-5 update: `_require_defined` itself never checked dotted access -- it always let evaluation
handle it, and `Context.get_dotted` (mohio_interpreter.py:751) is where T0-5 later drew the
real distinction FORK-5 ruled for: a field that is a real key (even holding None) stays exempt;
a field that was NEVER a key at all now fails loud there instead of silently returning None.
This file's "dotted field access is exempt" case was updated accordingly -- see the save-field
block below.

Run as a script: `python tests/test_undefined_value_positions.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
UV = "unknown variable"

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

def fails_loud(label, src, needle=UV):
    c, o = _run(src); _record(label, c != 0 and needle in o, f"exit={c}\n{o[-260:]}")

def runs_ok(label, src, needle):
    c, o = _run(src); _record(label, c == 0 and needle in o, f"exit={c}\n{o[-260:]}")

CONN = "connect db as sqlite from env.X\n"

# ---- save field: the live-corruption case, proven hardest ----
fails_loud("save: undefined bare name fails loud (no None written)",
           CONN + 'save to db.t\n    n nobody\nsave: done\n')
runs_ok("save: a defined value writes it", 'hold who "Bo"\n' + CONN +
        'save to db.t\n    n who\nsave: done\n', "'n': 'Bo'")
runs_ok("save: a defined-but-EMPTY value still writes (defined passes)", 'hold who ""\n' + CONN +
        'save to db.t\n    n who\nsave: done\n', "'n': ''")
runs_ok("save: a literal writes", CONN + 'save to db.t\n    n "Aria"\nsave: done\n', "'n': 'Aria'")
# T0-5 (FORK-5, ruled): this assertion locked in the exact bug T0-5 fixed and is corrected here,
# not weakened -- `obj.missing` where `obj` is a REAL dict (`{'a': 'x'}`) and `missing` was never
# one of its keys is the unknown-field case get_dotted now fails loud on (mohio_interpreter.py:
# 751), same as `p.nmae` on a retrieved row. `_require_defined` (Workstream A/A3, this file's own
# subject) never checked dotted access itself -- it always punted to evaluation, and evaluation is
# exactly what T0-5 changed. The load-bearing claim this test exists to prove -- a field that
# GENUINELY holds None (not one that never existed) does not block a save -- still holds; the case
# below proves that with a field that IS a real key, holding None, instead of one that was never a
# key at all.
fails_loud("save: a dotted access to a field that never existed (unknown, not empty) fails loud",
           'create obj\n    a "x"\ncreate: done\n' + CONN +
           'save to db.t\n    v obj.missing\nsave: done\n', "not a field on 'obj'")
runs_ok("save: a dotted access to a field that IS a real key, holding None, is exempt (may "
        "legitimately be null), still saves",
        'create obj\n    a "x"\n    b none\ncreate: done\n' + CONN +
        'save to db.t\n    v obj.b\nsave: done\n', "'v': None")

# ---- hold source ----
fails_loud("hold: undefined source fails loud", 'hold x nobody\nshow x\n')
runs_ok("hold: a `default` is preserved (undefined source -> default)",
        'hold x nobody default 5\nshow x\n', "5")
runs_ok("hold: a defined source binds", 'hold who "Bo"\nhold x who\nshow x\n', "Bo")

# ---- task argument (inline + named) ----
GREET = ('task greet\n    take who as text\n    give back ("hi " & who)\ntask: done\n')
fails_loud("task arg (inline): undefined fails loud", GREET + 'call greet with nobody\n')
runs_ok("task arg (inline): defined works", 'hold who "Bo"\n' + GREET +
        'call greet with who\n', "hi Bo")
fails_loud("task arg (named): undefined fails loud", GREET + 'call greet\n    who nobody\ncall: done\n')
runs_ok("task arg (named): defined works", 'hold name "Bo"\n' + GREET +
        'call greet\n    who name\ncall: done\n', "hi Bo")

# ---- load-bearing: a `when empty` check on an undefined subject is NOT our scope and
# must be UNCHANGED (still takes the empty branch, no fail-loud from A3). ----
runs_ok("`check X when empty` on an undefined subject is unchanged (not over-reached)",
        'check nobody\n    when nobody is empty\n        show "empty-branch"\n'
        '    otherwise\n        show "has"\ncheck: done\n', "empty-branch")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
