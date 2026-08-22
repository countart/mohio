# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-GUARD-FAILOPEN, Part A -- Postgres/MySQL sibling of test_guard_failopen_retrieve.py.

No live Postgres/MySQL server is available in this environment (test_postgres_backend.py
documents the same constraint and SKIPS cleanly without MOHIO_PG_TEST_DSN), so this verifies
the removed carve-outs directly against PostgresRuntime/MySQLRuntime using a mock cursor/
connection that raises the real driver exception shape (pgcode 42P01 / errno 1146, and a
genuinely different error code each backend must ALSO now propagate). This is a deliberate,
labeled unit test of the backend classes in isolation (permitted under
T1-TEST-REAL-PATH-STANDARD for a helper that cannot be reached without infrastructure this
environment doesn't have); test_guard_failopen_retrieve.py covers the real .mho pipeline
against SQLite, which every one of these methods shares its logic shape with.

Run: `python tests/test_guard_failopen_pg_mysql.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
from mohio_interpreter import PostgresRuntime, MySQLRuntime

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class FakeCursor:
    def __init__(self, exc): self._exc = exc
    def execute(self, *a, **k): raise self._exc
    def close(self): pass

class FakeConn:
    def __init__(self, exc):
        self._exc = exc
        self.rolled_back = False
    def cursor(self, cursor_factory=None): return FakeCursor(self._exc)
    def rollback(self): self.rolled_back = True


class PgMissingTable(Exception):
    pgcode = '42P01'

class PgMissingColumn(Exception):
    # UndefinedColumn -- the OLD carve-out's broad 'does not exist' string match would have
    # silently swallowed this too, not just a missing table. A real, separate bug this
    # removal also closes.
    pgcode = '42703'

class MySQLMissingTable(Exception):
    def __init__(self): super().__init__(1146, "Table 'x.ghost' doesn't exist")

class MySQLOtherError(Exception):
    def __init__(self): super().__init__(1054, "Unknown column 'z' in 'where clause'")


for name, exc_factory in (('missing table', PgMissingTable), ('missing COLUMN', PgMissingColumn)):
    for method, args in (
        ('retrieve_one', ('t', 'id', 1)),
        ('retrieve_one_multi', ('t', {'id': 1})),
        ('retrieve_one_spec', ('t', [('and', [('id', 1)])])),
        ('retrieve_all_spec', ('t', [])),
    ):
        pg = PostgresRuntime.__new__(PostgresRuntime)
        exc = exc_factory()
        pg.conn = FakeConn(exc)
        pg._cursor_factory = None
        try:
            getattr(pg, method)(*args)
            check(f"Postgres {method}, {name}: raises (not silently swallowed)", False)
        except type(exc):
            check(f"Postgres {method}, {name}: raises (not silently swallowed)", True)
        if method == 'retrieve_one':
            check(f"Postgres {method}, {name}: connection rolled back after the error",
                  pg.conn.rolled_back)

for name, exc_factory in (('missing table', MySQLMissingTable), ('genuine other error', MySQLOtherError)):
    for method, args in (
        ('retrieve_one', ('t', 'id', 1)),
        ('retrieve_one_multi', ('t', {'id': 1})),
        ('retrieve_one_spec', ('t', [('and', [('id', 1)])])),
        ('retrieve_all_spec', ('t', [])),
    ):
        my = MySQLRuntime.__new__(MySQLRuntime)
        exc = exc_factory()
        my.conn = FakeConn(exc)
        try:
            getattr(my, method)(*args)
            check(f"MySQL {method}, {name}: raises (not silently swallowed)", False)
        except type(exc):
            check(f"MySQL {method}, {name}: raises (not silently swallowed)", True)
        if method == 'retrieve_one':
            check(f"MySQL {method}, {name}: connection rolled back after the error",
                  my.conn.rolled_back)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
