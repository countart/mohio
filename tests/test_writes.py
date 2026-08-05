#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for the write side of the database: `save`, `update`, `remove`.

These are the writes every app makes. Verified here:

  save
  1. save inserts a row (static fields:  name "Alice")
  2. save reads a value from a held variable
  3. on.success fires after a successful save
  4. on.failure fires when the save errors (and the error is reachable)
  5. without on.failure, a save error still fails loud (backward compatible)

  update
  6. update changes only the matched row(s)
  7. on.success fires after a successful update

  remove
  8. remove deletes by match
  9. remove deletes by where
  10. on.success fires after a successful remove

REGRESSION NOTE: handlers (on.success / on.failure) used to be silently dropped
by the save_block and update_block transformers — present in the source, absent
from the AST. This suite guards against that ever returning.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, DbRuntime, _Raise

_raw = Path('mohio.lark').read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def fresh(unique_email=False):
    db = DbRuntime(':memory:')
    email_col = "email TEXT UNIQUE" if unique_email else "email TEXT"
    db.conn.execute(f"CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, {email_col}, status TEXT)")
    db.conn.executemany("INSERT INTO users(name,email,status) VALUES (?,?,?)",
                        [("Alice", "a@x.com", "new"), ("Bob", "b@x.com", "new")])
    db.conn.commit()
    it = MohioInterpreter(); it._db = db
    return it

def run(it, prog):
    it.shown = []
    t = transform(P.parse(H + prog), H + prog); it.run_declarations(t); it.run(t)
    return it

# ── save ──────────────────────────────────────────────────────
it = fresh()
run(it, 'save to db.users\n    name "Cara"\nsave: done\n')
n = it._db.conn.execute("SELECT COUNT(*) FROM users WHERE name='Cara'").fetchone()[0]
check("save inserts a row", n == 1)

it = fresh()
run(it, 'hold who = "Dave"\nsave to db.users\n    name who\nsave: done\n')
n = it._db.conn.execute("SELECT COUNT(*) FROM users WHERE name='Dave'").fetchone()[0]
check("save reads a value from a variable", n == 1)

it = fresh()
out = run(it, 'save to db.users\n    name "Eve"\n    on.success\n        show "saved"\nsave: done\n').shown
check("save on.success fires", out == ["saved"])

it = fresh(unique_email=True)
out = run(it, 'save to db.users\n    email "a@x.com"\n    on.failure\n        show "save-failed"\nsave: done\n').shown
check("save on.failure fires on db error", out == ["save-failed"])

def save_fails_loud():
    it = fresh(unique_email=True)
    node = transform(P.parse('save to db.users\n    email "a@x.com"\nsave: done\n'),
                     'save to db.users\n    email "a@x.com"\nsave: done\n').statements[0]
    ctx = Context(); ctx.set_connection('db', it._db)
    try:
        it._exec(node, ctx); return False
    except _Raise as e:
        return e.error_name == 'db_error'
check("save without on.failure still fails loud", save_fails_loud())

# ── update ────────────────────────────────────────────────────
it = fresh()
run(it, 'update db.users\n    match name to "Alice"\n    status "active"\nupdate: done\n')
alice = it._db.conn.execute("SELECT status FROM users WHERE name='Alice'").fetchone()[0]
bob   = it._db.conn.execute("SELECT status FROM users WHERE name='Bob'").fetchone()[0]
check("update changes only the matched row", alice == "active" and bob == "new")

it = fresh()
out = run(it, 'update db.users\n    match name to "Alice"\n    status "active"\n    on.success\n        show "updated"\nupdate: done\n').shown
check("update on.success fires", out == ["updated"])

# ── remove ────────────────────────────────────────────────────
it = fresh()
run(it, 'remove from db.users\n    match name to "Bob"\nremove: done\n')
n = it._db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
check("remove deletes by match", n == 1)

it = fresh()
run(it, 'remove from db.users\n    where name is "Alice"\nremove: done\n')
gone = it._db.conn.execute("SELECT COUNT(*) FROM users WHERE name='Alice'").fetchone()[0]
check("remove deletes by where", gone == 0)

it = fresh()
out = run(it, 'remove from db.users\n    match name to "Bob"\n    on.success\n        show "removed"\nremove: done\n').shown
check("remove on.success fires", out == ["removed"])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
