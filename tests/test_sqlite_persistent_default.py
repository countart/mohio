# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The CLI SQLite default must be a persistent file, with :memory: reachable only on purpose.

Locks _resolve_sqlite_db_path: --memory -> throwaway, --db -> explicit, DATABASE_URL ->
honored (including an explicit :memory:), nothing -> a persistent file under ~/.mohio/data/.

Run: PYTHONPATH=$PWD python3 tests/test_sqlite_persistent_default.py
"""
import os, sys
from argparse import Namespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mio import _resolve_sqlite_db_path

_passed = _failed = 0
def check(label, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok   {label}")
    else: _failed += 1; print(f"  FAIL {label}  {detail}")

def with_database_url(val):
    if val is None: os.environ.pop('DATABASE_URL', None)
    else: os.environ['DATABASE_URL'] = val

MHO = "/tmp/some_app.mho"

# 1. --memory wins over everything -> throwaway
with_database_url("/should/be/ignored.db")
r = _resolve_sqlite_db_path(MHO, Namespace(memory=True, db=None))
check("--memory -> :memory:", r == ':memory:', r)

# 2. --db is explicit
with_database_url(None)
r = _resolve_sqlite_db_path(MHO, Namespace(memory=False, db="/explicit/path.db"))
check("--db -> explicit path", r == "/explicit/path.db", r)

# 3. DATABASE_URL honored when no flags
with_database_url("/env/chosen.db")
r = _resolve_sqlite_db_path(MHO, Namespace(memory=False, db=None))
check("DATABASE_URL -> honored", r == "/env/chosen.db", r)

# 4. explicit DATABASE_URL=:memory: is honored throwaway (this is what the test suites use)
with_database_url(":memory:")
r = _resolve_sqlite_db_path(MHO, Namespace(memory=False, db=None))
check("DATABASE_URL=:memory: -> honored throwaway", r == ':memory:', r)

# 5. nothing chosen -> persistent file under ~/.mohio/data/, keyed to the program
with_database_url(None)
r = _resolve_sqlite_db_path(MHO, Namespace(memory=False, db=None))
data_dir = os.path.join(os.path.expanduser('~'), '.mohio', 'data')
check("no choice -> under ~/.mohio/data/", r.startswith(data_dir), r)
check("no choice -> is a .db file", r.endswith('.db'), r)
check("no choice -> keyed to the program name", os.path.basename(r).startswith('some_app-'), r)
check("no choice -> the data dir was created", os.path.isdir(data_dir), data_dir)

# 6. stable: same program resolves to the same file across calls
r2 = _resolve_sqlite_db_path(MHO, Namespace(memory=False, db=None))
check("no choice -> stable across calls for the same program", r == r2, f"{r} vs {r2}")

# 7. different programs resolve to different files
r3 = _resolve_sqlite_db_path("/tmp/other_app.mho", Namespace(memory=False, db=None))
check("different program -> different file", r3 != r, f"{r} vs {r3}")

with_database_url(None)
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
