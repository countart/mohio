# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Workstream A / Unit A3.1 -- extend the A3 undefined-value guard across EVERY DB write
path, not just save. A3 hardened save / hold / task-arg; A3.1 covers the rest of the write
family after enumerating them: update, save-or-update (upsert, incl. the written match key),
save-all (the FROM collection), create, and modify.

An undefined bare name in any written field silently wrote None (or, for save-all, silently
saved nothing) -- the same live data-corruption class through a different verb. Each now fails
loud, nothing written. Defined / empty / literal / dotted values, and a modify field that
references a row column (resolved in the row scope), all still work.

Run as a script: `python tests/test_write_path_undefined_guard.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
UV = "unknown variable"
CONN = "connect db as sqlite from env.X\n"
SEED = CONN + 'save to db.t\n    n "seed"\nsave: done\n'

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

def fails(label, src, needle=UV):
    c, o = _run(src); _record(label, c != 0 and needle in o, f"exit={c}\n{o[-260:]}")

def ok(label, src):
    c, o = _run(src); _record(label, c == 0, f"exit={c}\n{o[-260:]}")

# ---- update ----
fails("update: undefined set field fails loud",
      SEED + 'update db.t\n    n nobody\n    match id to 1\nupdate: done\n')
ok("update: a literal set field works",
   SEED + 'update db.t\n    n "new"\n    match id to 1\nupdate: done\n')

# ---- save or update (upsert): set field AND the written match key ----
fails("upsert: undefined set field fails loud",
      CONN + 'save or update db.t\n    match id to "1"\n    n nobody\nsave: done\n')
fails("upsert: undefined match value (written into the row) fails loud",
      CONN + 'save or update db.t\n    match id to nobody\n    n "x"\nsave: done\n')
ok("upsert: defined values work",
   CONN + 'save or update db.t\n    match id to "1"\n    n "x"\nsave: done\n')

# ---- save all: the FROM collection ----
fails("save all: undefined source collection fails loud (was: saved nothing silently)",
      CONN + 'save all to db.t from nobody\nsave: done\n')
ok("save all: a defined collection works",
   CONN + 'save to db.src\n    n "a"\nsave: done\nfind rows in db.src\nfind: done\n'
          'save all to db.dst from rows\nsave: done\n')

# ---- create ----
fails("create: undefined field fails loud", 'create thing\n    n nobody\ncreate: done\n')
ok("create: a literal field works", 'create thing\n    n "x"\ncreate: done\nshow thing\n')

# ---- modify ----
fails("modify: undefined field fails loud",
      SEED + 'modify every r in db.t\n    apply r\n        n nobody\n    apply: done\nmodify: done\n')
ok("modify: a literal field works",
   SEED + 'modify every r in db.t\n    apply r\n        n "z"\n    apply: done\nmodify: done\n')
# load-bearing: a modify field that references the row's own column (bound in the row scope)
# must NOT be flagged -- it is defined there.
ok("modify: a field referencing the row's own column still works (row scope)",
   SEED + 'modify every r in db.t\n    apply r\n        n n\n    apply: done\nmodify: done\n')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
