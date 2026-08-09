# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Structural coverage for classify_sink's grading paths (GAP-1 treatment, applied here too).

`classify_sink` is the other compliance discriminant guard with sibling paths: None, a
provider-verified grade, an ephemeral in-memory sqlite, an on-disk sqlite, each networked engine
(postgres, mysql/maria), and the unrecognised fallback. Mutation testing (2026-07-31) found the
same disease the tombstone verifier had: ONE sibling (ephemeral sqlite -> none) was tested, and
misgrading postgres, mysql, OR an unrecognised sink survived the whole suite. The worst miss was
an UNRECOGNISED sink graded `durable` -- a store that cannot be verified silently passing as
compliant, the exact "grade reads true until someone checks" failure this module warns about.

This test asserts the grade for every path, and DERIVES the networked-engine family from the code
(the `"<engine>" in name` checks), so adding an engine automatically tests it -- or, if it grades
something unexpected, fails the build. The one invariant that must never rot: a sink whose
durability cannot be established is graded "none", never higher.

Run as a script: `python tests/test_audit_sink_grading_paths.py` (exit 0 = pass).
"""
import os, sys, re, inspect, sqlite3, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)

from mohio_audit_grades import classify_sink

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def grade(sink):
    g, durable, _why = classify_sink(sink)
    return g, durable

class _Sink:
    def __init__(self, conn): self.conn = conn

class _RaisingConn:
    """A connection that is NOT sqlite-inspectable, so classify_sink falls through to the
    type-name engine checks (as a real psycopg2/pymysql connection would)."""
    def execute(self, *a, **k): raise RuntimeError("not a sqlite connection")

_tmp = []
def _disk_sqlite():
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd); _tmp.append(path)
    return _Sink(sqlite3.connect(path))

# --- fixed paths --------------------------------------------------------------------------
check("None sink -> (none, not durable)", grade(None) == ("none", False), str(grade(None)))

class _NoConn: pass
check("object with no connection -> (none, not durable)",
      grade(_NoConn()) == ("none", False), str(grade(_NoConn())))

mem = _Sink(sqlite3.connect(':memory:'))
check("ephemeral in-memory sqlite -> (none, not durable)  [silent-non-durability guard]",
      grade(mem) == ("none", False), str(grade(mem)))

disk = _disk_sqlite()
check("on-disk sqlite -> (durable, durable)", grade(disk) == ("durable", True), str(grade(disk)))

# provider-verified channel: each asserted grade is honoured, and ONLY through this channel.
for gv in ("durable", "append_only", "worm"):
    s = _Sink(sqlite3.connect(':memory:')); s._mohio_grade_verified = gv
    check(f"provider-verified '{gv}' -> ({gv}, durable)", grade(s) == (gv, True), str(grade(s)))

# --- the networked-engine family, DERIVED from the code -----------------------------------
# Every `"<engine>" in name` check is a sibling path; enumerate them from source so a new engine
# is tested automatically instead of silently uncovered.
src = inspect.getsource(classify_sink)
ENGINES = sorted(set(re.findall(r'"([a-z]+)" in name', src)))
check("derived a non-empty networked-engine family from the code", len(ENGINES) >= 1, str(ENGINES))
print(f"    engine substrings the code recognises: {ENGINES}")
for eng in ENGINES:
    sink = type(f"conn_{eng}_engine", (), {"conn": _RaisingConn()})()   # class name contains `eng`
    g = grade(sink)
    check(f"[{eng}] a recognised networked engine -> durable", g == ("durable", True), str(g))

# --- the invariant that must never rot ----------------------------------------------------
# An unrecognised sink (no verified grade, not sqlite, not a known engine) cannot be established as
# durable, so it MUST grade "none". Grading it higher is the C-d miss: an ungradeable store passing
# as compliant. This is the branch no test caught before today.
mystery = type("SomeUnknownStore", (), {"conn": _RaisingConn()})()
g = grade(mystery)
check("unrecognised sink -> (none, not durable)  [must never be graded compliant]",
      g == ("none", False), str(g))

for pth in _tmp:
    try: os.unlink(pth)
    except Exception: pass

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
