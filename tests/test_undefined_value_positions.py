# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Workstream A / Unit A3 -- an undefined bare variable used as a VALUE fails loud
instead of silently resolving to None, in the three positions where that silence bites:

  * save field   -- `save to db.t / n nobody` silently WROTE None (live data corruption)
  * hold source  -- `hold x nobody` silently bound None
  * task argument -- `call greet with nobody` silently passed None

It reuses the same unknown_variable rule `show` and interpolation already enforce, and ONLY
catches a lone undefined name. The load-bearing exemptions are proven too: a defined value
(even empty), a dotted field access (which may legitimately be null), a `default`, and a
`when empty` check on an undefined subject all keep working -- ctx.get and its None are
untouched.

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
# dotted field access is EXEMPT (a field may legitimately be null); must NOT fail loud.
runs_ok("save: a dotted field access is exempt (may be null), still saves",
        'create obj\n    a "x"\ncreate: done\n' + CONN +
        'save to db.t\n    v obj.missing\nsave: done\n', "'v': None")

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
