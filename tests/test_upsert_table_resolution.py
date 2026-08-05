# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""upsert table resolution -- the Zork-breaking bug.

`upsert db.saved_games` resolved its table to the invented name "unknown" through the real
`mio run` path (the pre-tokenizer rewrote `db.saved_games` into a DottedName, which
save_or_update_block did not handle, so `source` was None and `_resolve_source` fell back to
"unknown"). On SQLite that silently created/wrote a table named `unknown`; on Postgres it
failed loudly on `ON CONFLICT` with no unique index -- the live Zork error.

Two fixes: save_or_update_block now accepts a DottedName source (like save/find already do),
and `_resolve_source` FAILS LOUD instead of inventing a table when the source is unresolvable.

Run as a script: `python tests/test_upsert_table_resolution.py` (exit 0 = pass).
"""
import os, sqlite3, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")

_p = _f = 0
def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

PROBE = (
    "connect db as sqlite from env.DATABASE_URL\n"
    "upsert db.saved_games\n"
    '    match session_id to "probe1"\n'
    '    current_room "x"\n'
    "upsert: done\n"
)

def _run_probe(db_path):
    fd, mho = tempfile.mkstemp(suffix=".mho"); os.write(fd, PROBE.encode()); os.close(fd)
    env = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=db_path,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run([sys.executable, MIO, "run", mho], cwd=REPO, env=env,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(mho)

def _tables(db_path):
    c = sqlite3.connect(db_path)
    try:
        return sorted(row[0] for row in
                      c.execute("select name from sqlite_master where type='table'"))
    finally:
        c.close()

# ---- the repro, end to end through mio run ----
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "probe.db")
code, out = _run_probe(db)
tables = _tables(db)
_record("upsert creates the named table `saved_games`", "saved_games" in tables, f"tables={tables}")
_record("upsert does NOT create an invented `unknown` table", "unknown" not in tables,
        f"tables={tables}\n{out[-200:]}")
rows = list(sqlite3.connect(db).execute("select session_id, current_room from saved_games"))
_record("the row landed in saved_games with the right values",
        rows == [("probe1", "x")], f"rows={rows}")

# ---- idempotency: run again -> upsert updates, does not duplicate ----
_run_probe(db)
n = list(sqlite3.connect(db).execute("select count(*) from saved_games"))[0][0]
_record("a second upsert on the same match key updates, not duplicates (1 row)", n == 1, f"count={n}")

# ---- fail-loud safety net (in process): unresolvable source never invents a table ----
import mohio_interpreter as M
from mohio_ast import DbRef, DottedName
interp = M.MohioInterpreter()
for bad in (None, "", "db.", "   ", 0):
    try:
        got = interp._resolve_source(bad, None)
        _record(f"_resolve_source({bad!r}) fails loud", False, f"returned {got!r}, should raise")
    except M.MohioRuntimeError:
        _record(f"_resolve_source({bad!r}) fails loud", True)
for good, exp in [(DbRef(table="saved_games"), "saved_games"),
                  (DottedName(parts=["db", "saved_games"]), "saved_games"),
                  ("db.saved_games", "saved_games"), ("players", "players")]:
    got = interp._resolve_source(good, None)
    _record(f"_resolve_source resolves {type(good).__name__ if not isinstance(good,str) else repr(good)} -> {exp}",
            got == exp, f"got {got!r}")

# ---- the node is canonical + picklable (no ad-hoc _Match local class) ----
import pickle, io, contextlib
from mohio_transformer_ast import transform
from mio import _load_grammar, _make_parser_cached
import mio
_src = ('connect db as sqlite from env.X\nupsert db.t\n'
        '    match id to "1"\n    n "x"\nupsert: done\n')
_p2 = _make_parser_cached(_load_grammar())
_sou = [s for s in transform(_p2.parse(_src), _src).statements
        if type(s).__name__ == 'SaveOrUpdateBlock'][0]
_record("upsert match uses the canonical MatchClause (not an ad-hoc local class)",
        type(_sou.match).__name__ == 'MatchClause', f"got {type(_sou.match).__name__}")
try:
    pickle.dumps(_sou); _pick_ok = True; _pick_err = ""
except Exception as e:
    _pick_ok = False; _pick_err = str(e)[:80]
_record("the upsert AST node is picklable", _pick_ok, _pick_err)

# ---- the AST-cache write failure is VISIBLE, not silently swallowed ----
class _Unpicklable:
    def __reduce__(self): raise TypeError("deliberately unpicklable")
class _NoErrCtx:
    errors = None
_buf = io.StringIO()
with contextlib.redirect_stderr(_buf):
    mio._save_ast_cache(os.path.join(tempfile.mkdtemp(), "x.mho"), "src",
                        _Unpicklable(), _NoErrCtx())
_record("a cache-write failure prints a warning (no longer a silent swallow)",
        "[ast-cache] could not write" in _buf.getvalue(), repr(_buf.getvalue()[:100]))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
