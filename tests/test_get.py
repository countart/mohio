#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Lock tests for single-record fetch: `get` and `grab` (`get` is an alias of
`grab` at runtime). This is the read complement to `find` — find returns a
collection, get/grab return one record by an exact match.

  1. get/grab build GetBlock/GrabBlock — NOT a raw Tree
     (regression guard: both used to drop to raw Trees with no transformer,
      leaving their executors dead)
  2. get fetches a record by id
  3. get fetches by any field (email)
  4. grab (the alias) fetches the same way
  5. a miss binds nothing (None) — no error, so `if x is none` stays valid
  6. on.success fires when a record is found
  7. on.failure does NOT fire on a miss (T1-GUARD-FAILOPEN Part B, 2026-08-19 --
     supersedes the old "fetch-or-404" pattern this test used to lock: on.failure is now
     reserved for a genuine driver error, matching retrieve's RUN-1 ruling; a real miss is
     a legitimate empty result and runs the normal when/otherwise path instead)
  8. a miss without on.failure binds None and does not error
  9. a real miss still fires when-empty/otherwise (the new, correct not-found channel)
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def fresh():
    db = DbRuntime(':memory:')
    db.conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    db.conn.executemany("INSERT INTO users(name,email) VALUES (?,?)",
                        [("Alice", "a@x.com"), ("Bob", "b@x.com")])
    db.conn.commit()
    it = MohioInterpreter(); it._db = db
    return it

def run(prog):
    it = fresh(); it.shown = []
    t = transform(P.parse(H + prog), H + prog); it.run_declarations(t); it.run(t)
    return it.shown

# 1. regression guard — real nodes, not Trees
gt = transform(P.parse('get u from db.users\n    match id to 1\nget: done\n'), '').statements[0]
gr = transform(P.parse('grab u from db.users\n    match id to 1\ngrab: done\n'), '').statements[0]
check("get builds GetBlock (not a raw Tree)", type(gt).__name__ == 'GetBlock')
check("grab builds GrabBlock (not a raw Tree)", type(gr).__name__ == 'GrabBlock')

# 2-4. fetching
check("get fetches by id", run('get u from db.users\n    match id to 1\nget: done\nshow u.name\n') == ["Alice"])
check("get fetches by another field", run('get u from db.users\n    match email to "b@x.com"\nget: done\nshow u.name\n') == ["Bob"])
check("grab fetches the same way", run('grab u from db.users\n    match id to 2\ngrab: done\nshow u.name\n') == ["Bob"])

# 5. miss binds None
check("a miss binds None", run('get u from db.users\n    match id to 999\nget: done\nshow u\n') == [None])

# 6-7. handlers
check("on.success fires when found",
      run('get u from db.users\n    match id to 1\n    on.success\n        show "found"\nget: done\n') == ["found"])
check("on.failure does NOT fire on a real miss (superseded fetch-or-404 pattern)",
      run('get u from db.users\n    match id to 999\n    on.failure\n        show "not-found"\nget: done\n') == [])

# 8. miss without handler does not error
ok = True
try:
    run('get u from db.users\n    match id to 999\nget: done\nshow u\n')
except Exception:
    ok = False
check("a miss without on.failure does not error", ok)

# 9. a real miss fires when-empty/otherwise -- the new, correct not-found channel
check("a real miss fires when-empty (the new not-found channel, RUN-1/Part-B consistent)",
      run('get u from db.users\n    match id to 999\n'
          '    when u is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'
          'get: done\n') == ["MISS"])
check("found still fires otherwise/HIT (regression guard)",
      run('get u from db.users\n    match id to 1\n'
          '    when u is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'
          'get: done\n') == ["HIT"])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
