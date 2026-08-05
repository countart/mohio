#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
MySQL / MariaDB backend verification — pagination, writes, and single-record
fetch run against a REAL MySQL-family server, not sqlite.

OPTIONAL test: SKIPS cleanly (exit 0) when MySQL or pymysql is unavailable, so it
never breaks the sqlite suite. To run it, point it at a MySQL/MariaDB instance:

    MOHIO_MYSQL_TEST_DSN="mysql://user:pass@host:3306/dbname"
    PYTHONPATH=<root> python3 tests/test_mysql_backend.py

What it proves: the per-backend MySQL find_many(offset)/count + save/update/
remove + retrieve_one paths behave identically to sqlite and Postgres. This is
the suite that caught the MySQL count() DictCursor bug (count read fetchone()[0]
on a dict), so it stays as a permanent guard for that path.

Note on DDL differences from Postgres: MySQL uses INTEGER PRIMARY KEY
AUTO_INCREMENT (not SERIAL), and a uniquely-indexed string column must be a
sized VARCHAR (not TEXT).
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DSN = os.environ.get('MOHIO_MYSQL_TEST_DSN')
if not DSN:
    print("SKIP  test_mysql_backend: set MOHIO_MYSQL_TEST_DSN to a MySQL DSN to run")
    sys.exit(0)
try:
    import pymysql  # noqa: F401
except Exception:
    print("SKIP  test_mysql_backend: pymysql not installed")
    sys.exit(0)

import urllib.parse as _up
_p = _up.urlparse(DSN)
_CONN = dict(host=_p.hostname or 'localhost', port=_p.port or 3306,
             user=_p.username, password=_p.password,
             database=_p.path.lstrip('/'), autocommit=True)

# The program's `connect ... from env.MYSQL_URL` reads MYSQL_URL (or DATABASE_URL).
os.environ['MYSQL_URL'] = DSN
os.environ['DATABASE_URL'] = DSN

try:
    _c = pymysql.connect(**_CONN); _c.close()
except Exception as e:
    print(f"SKIP  test_mysql_backend: cannot connect ({str(e).splitlines()[0][:50]})")
    sys.exit(0)

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as mysql from env.MYSQL_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def _seed(sqls):
    c = pymysql.connect(**_CONN); cur = c.cursor()
    for s in sqls: cur.execute(s)
    cur.close(); c.close()

def products():
    _seed(["DROP TABLE IF EXISTS products",
           "CREATE TABLE products (id INTEGER PRIMARY KEY AUTO_INCREMENT, "
           "name VARCHAR(40), category VARCHAR(20), price INTEGER)"]
          + [f"INSERT INTO products(name,category,price) VALUES "
             f"('P{i:02d}','{'books' if i % 2 else 'toys'}',{i*5})" for i in range(1, 26)])
    return MohioInterpreter()

def users(unique=False):
    ec = "email VARCHAR(255) UNIQUE" if unique else "email VARCHAR(255)"
    _seed(["DROP TABLE IF EXISTS users",
           f"CREATE TABLE users (id INTEGER PRIMARY KEY AUTO_INCREMENT, "
           f"name VARCHAR(40), {ec}, status VARCHAR(20))",
           "INSERT INTO users(name,email,status) VALUES ('Alice','a@x.com','new')",
           "INSERT INTO users(name,email,status) VALUES ('Bob','b@x.com','new')"])
    return MohioInterpreter()

def run(it, prog):
    it.shown = []
    t = transform(P.parse(H + prog), H + prog); it.run_declarations(t); it.run(t)
    out = list(it.shown)
    try:
        it._db.conn.commit(); it._db.conn.close()
    except Exception:
        pass
    return out

# ── pagination (this is the path that exposed the count DictCursor bug) ──
check("my: count all", run(products(), 'find x in db.products\nfind: done\nshow x.count\n') == [25])
check("my: where filter count", run(products(), 'find x in db.products\n    where category is "books"\nfind: done\nshow x.count\n') == [13])
check("my: order.up first", run(products(), 'find x in db.products\n    order.up by price\nfind: done\nshow x.first.price\n') == [5])
check("my: paginate total", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.total\n') == [3])
check("my: paginate has_more p1", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.has_more\n') == [True])
check("my: paginate has_more p3", run(products(), 'find x in db.products\n    up to 10\n    paginate by 3\nfind: done\nshow x.page.has_more\n') == [False])
check("my: page 2 slice", run(products(), 'find x in db.products\n    order.up by price\n    up to 10\n    paginate by 2\nfind: done\nshow x.first.price\n') == [55])
check("my: partial last page", run(products(), 'find x in db.products\n    up to 10\n    paginate by 3\nfind: done\nshow x.count\n') == [5])
check("my: skip 5 ordered", run(products(), 'find x in db.products\n    order.up by price\n    skip 5\nfind: done\nshow x.first.price\n') == [30])
check("my: page.count total", run(products(), 'find x in db.products\n    up to 10\n    paginate by 1\nfind: done\nshow x.page.count\n') == [25])

# ── writes + handlers ───────────────────────────────────────────────
it = users(); run(it, 'save to db.users\n    name "Cara"\nsave: done\n')
check("my: save inserts", run(it, 'find u in db.users\n    where name is "Cara"\nfind: done\nshow u.count\n') == [1])
check("my: save on.success", run(users(), 'save to db.users\n    name "Fay"\n    on.success\n        show "saved"\nsave: done\n') == ["saved"])
check("my: save on.failure (unique violation)",
      run(users(unique=True), 'save to db.users\n    email "a@x.com"\n    on.failure\n        show "dup"\nsave: done\n') == ["dup"])
it = users(); run(it, 'update db.users\n    match name to "Alice"\n    status "active"\nupdate: done\n')
check("my: update scoped to match", run(it, 'find u in db.users\n    where name is "Bob"\nfind: done\nshow u.first.status\n') == ["new"])
check("my: update on.success", run(users(), 'update db.users\n    match name to "Alice"\n    status "active"\n    on.success\n        show "upd"\nupdate: done\n') == ["upd"])
it = users(); run(it, 'remove from db.users\n    match name to "Bob"\nremove: done\n')
check("my: remove by match", run(it, 'find u in db.users\nfind: done\nshow u.count\n') == [1])

# ── single-record fetch ─────────────────────────────────────────────
check("my: get by id", run(users(), 'get u from db.users\n    match id to 1\nget: done\nshow u.name\n') == ["Alice"])
check("my: get miss on.failure", run(users(), 'get u from db.users\n    match id to 999\n    on.failure\n        show "404"\nget: done\n') == ["404"])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
