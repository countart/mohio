#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Postgres backend verification — pagination, writes, and single-record fetch run
against a REAL Postgres server (the production database engine), not sqlite.

This is an OPTIONAL test: it SKIPS cleanly (exit 0) when Postgres or psycopg2 is
not available, so it never breaks the normal sqlite-based suite. To run it,
point it at a Postgres instance via either env var:

    MOHIO_PG_TEST_DSN="host=/tmp/pgrun port=5433 dbname=mohiotest user=postgres"
    # or a URL:
    MOHIO_PG_TEST_DSN="postgresql://user:pass@host:5432/dbname"

Then:
    PYTHONPATH=<root> python3 tests/test_postgres_backend.py

What it proves: the per-backend Postgres find_many(offset)/count + save/update/
remove + retrieve_one paths behave identically to the sqlite path that the rest
of the suite locks. The pagination LOGIC itself lives in the shared
_finalize_rows (Python), so this confirms the Postgres SQL wiring around it.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)

DSN = os.environ.get('MOHIO_PG_TEST_DSN')
if not DSN:
    print("SKIP  test_postgres_backend: set MOHIO_PG_TEST_DSN to a Postgres DSN to run")
    sys.exit(0)
try:
    import psycopg2  # noqa: F401
except Exception:
    print("SKIP  test_postgres_backend: psycopg2 not installed")
    sys.exit(0)

# The program's `connect ... from env.DATABASE_URL` reads DATABASE_URL, so point
# it at the same Postgres the test seeds. psycopg2 accepts URL or keyword DSN.
os.environ['DATABASE_URL'] = DSN

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, PostgresRuntime

try:
    _probe = PostgresRuntime(DSN); _probe.conn.close()
except Exception as e:
    print(f"SKIP  test_postgres_backend: cannot connect ({str(e).splitlines()[0][:50]})")
    sys.exit(0)

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as postgres from env.DATABASE_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def products():
    db = PostgresRuntime(DSN); db.conn.autocommit = True; cur = db.conn.cursor()
    cur.execute("DROP TABLE IF EXISTS products")
    cur.execute("CREATE TABLE products (id SERIAL PRIMARY KEY, name TEXT, category TEXT, price INTEGER)")
    cur.executemany("INSERT INTO products(name,category,price) VALUES (%s,%s,%s)",
                    [(f"P{i:02d}", "books" if i % 2 else "toys", i * 5) for i in range(1, 26)])
    cur.close(); db.conn.close()          # commit (autocommit) + release locks
    return MohioInterpreter()             # connect builds its own conn from DATABASE_URL

def users(unique=False):
    db = PostgresRuntime(DSN); db.conn.autocommit = True; cur = db.conn.cursor()
    cur.execute("DROP TABLE IF EXISTS users")
    ec = "email TEXT UNIQUE" if unique else "email TEXT"
    cur.execute(f"CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT, {ec}, status TEXT)")
    cur.executemany("INSERT INTO users(name,email,status) VALUES (%s,%s,%s)",
                    [("Alice", "a@x.com", "new"), ("Bob", "b@x.com", "new")])
    cur.close(); db.conn.close()
    return MohioInterpreter()

def run(it, prog):
    it.shown = []
    t = transform(P.parse(H + prog), H + prog); it.run_declarations(t); it.run(t)
    out = list(it.shown)
    try:                                  # commit writes, release locks, close
        it._db.conn.commit(); it._db.conn.close()
    except Exception:
        pass
    return out

# ── pagination ────────────────────────────────────────────────
check("pg: count all", run(products(), 'find x in db.products\nfind: done\nshow x.count\n') == [25])
check("pg: order.up first", run(products(), 'find x in db.products\n    order.up by price\nfind: done\nshow x.first.price\n') == [5])
check("pg: paginate total", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.total\n') == [3])
check("pg: paginate has_more p1", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.has_more\n') == [True])
check("pg: paginate has_more p3", run(products(), 'find x in db.products\n    up to 10\n    paginate by 3\nfind: done\nshow x.page.has_more\n') == [False])
check("pg: page 2 slice", run(products(), 'find x in db.products\n    order.up by price\n    up to 10\n    paginate by 2\nfind: done\nshow x.first.price\n') == [55])
check("pg: partial last page", run(products(), 'find x in db.products\n    up to 10\n    paginate by 3\nfind: done\nshow x.count\n') == [5])
check("pg: skip 5 ordered", run(products(), 'find x in db.products\n    order.up by price\n    skip 5\nfind: done\nshow x.first.price\n') == [30])
check("pg: page.count total", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.count\n') == [25])

# ── writes + handlers ─────────────────────────────────────────
it = users(); run(it, 'save to db.users\n    name "Cara"\nsave: done\n')
got = run(it, 'find u in db.users\n    where name is "Cara"\nfind: done\nshow u.count\n')
check("pg: save inserts", got == [1])
check("pg: save on.success", run(users(), 'save to db.users\n    name "Fay"\n    on.success\n        show "saved"\nsave: done\n') == ["saved"])
check("pg: save on.failure (unique violation)",
      run(users(unique=True), 'save to db.users\n    email "a@x.com"\n    on.failure\n        show "dup"\nsave: done\n') == ["dup"])
it = users(); run(it, 'update db.users\n    match name to "Alice"\n    status "active"\nupdate: done\n')
check("pg: update scoped to match",
      run(it, 'find u in db.users\n    where name is "Bob"\nfind: done\nshow u.first.status\n') == ["new"])
check("pg: update on.success", run(users(), 'update db.users\n    match name to "Alice"\n    status "active"\n    on.success\n        show "upd"\nupdate: done\n') == ["upd"])
it = users(); run(it, 'remove from db.users\n    match name to "Bob"\nremove: done\n')
check("pg: remove by match", run(it, 'find u in db.users\nfind: done\nshow u.count\n') == [1])

# ── single-record fetch ───────────────────────────────────────
check("pg: get by id", run(users(), 'get u from db.users\n    match id to 1\nget: done\nshow u.name\n') == ["Alice"])
check("pg: get miss on.failure", run(users(), 'get u from db.users\n    match id to 999\n    on.failure\n        show "404"\nget: done\n') == ["404"])

# ── aborted-transaction brick (the gating bug): a failed query must NOT
#    poison a persistent session connection. Uses ONE runtime across
#    read -> failed save -> read, without closing between calls. ──
def _brick_heal():
    db = PostgresRuntime(DSN); cur = db.conn.cursor()
    cur.execute("DROP TABLE IF EXISTS game")
    cur.execute("CREATE TABLE game (id SERIAL PRIMARY KEY, room TEXT)")
    cur.execute("INSERT INTO game(room) VALUES ('west_of_house')")
    db.conn.commit(); cur.close()
    before = db.retrieve_one_spec("game", {"id": 1})
    try:
        db.save("game", {"nonexistent_col": "boom"})   # forces an error mid-session
    except Exception:
        pass
    after_read = db.retrieve_one_spec("game", {"id": 1})   # must still return the row
    after_find = db.find_many("game")
    db.save("game", {"room": "north_of_house"})            # writes must work too
    total = len(db.find_many("game"))
    db.conn.close()
    return (before and after_read and after_read.get('room') == 'west_of_house'
            and isinstance(after_find, list) and len(after_find) >= 1 and total == 2)
check("pg: failed query does not brick session", _brick_heal())


sys.exit(1 if FAIL else 0)
