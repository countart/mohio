# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Audit-lock tests — runtime behaviors cleared/fixed during the no-debt audit.
Run: python3 tests/test_audit_locks.py   (from the compiler root)
Locks against silent regression of: default params, named-arg binding,
loop accumulation, skip/stop, find single+multi match, retrieve/update
multi-match, mask.all, no-arg call.
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

passed = failed = 0
def check(name, got, want):
    global passed, failed
    ok = (got == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r} want={want!r}"))
    passed += ok; failed += (not ok)

def run(src, seed=None):
    interp = MohioInterpreter()
    if seed is not None:
        db = DbRuntime(':memory:'); seed(db); interp._db = db
    tree = transform(P.parse(src), src)
    interp.run_declarations(tree)
    r = interp.run(tree)
    return getattr(r, 'value', r)

# --- tasks / call / params ---
BILL = 'task bill\n    take who as text\n    take amt as int default 0\n    returns text\n    give back (who & " owes " & amt)\ntask: done\n'
check("default param applies when omitted", run(BILL+'call bill\n    who "Bo"\ncall: done\n'), "Bo owes 0")
check("named args bind (no cross-contam)",  run(BILL+'call bill\n    who "Bo"\n    amt 50\ncall: done\n'), "Bo owes 50")
check("no-arg call (closer form)", run('task ping\n    returns text\n    give back ("pong")\ntask: done\ncall ping\ncall: done\n'), "pong")
check("call ... with inline", run('task greet\n    take name as text\n    returns text\n    give back ("Hi, " & name)\ntask: done\ncall greet with "Aria"\n'), "Hi, Aria")

# --- loops (Flow of Knowledge) ---
check("loop accumulation (name value)", run('total 0\nrepeat 3 times\n    total (total + 10)\nrepeat: done\nshow total\n'), 30)
check("stop when", run('n 0\nrepeat 100 times\n    n (n + 1)\n    stop when n > 2\nrepeat: done\nshow n\n'), 3)
check("skip when", run('total 0\nnums as list 1, 2, 3, 4\nrepeat each x in nums\n    skip when x < 3\n    total (total + x)\nrepeat: done\nshow total\n'), 7)

# --- mask.all (string fn) ---
check("mask.all except last 4", run('card_number "4111111111111111"\nshow (card_number mask.all except last 4)\n'), "************1111")

# --- find / retrieve / update match (seeded DB) ---
def seed_items(db):
    db.conn.execute("CREATE TABLE items (name TEXT, location TEXT)")
    db.conn.executemany("INSERT INTO items VALUES (?,?)",
        [('lantern','inventory'),('sword','inventory'),('rope','room')]); db.conn.commit()
FIND = ('connect db as sqlite from env.DATABASE_URL\nfind carrying in db.items\n'
        '    match location to "inventory"\nfind: done\nnames ""\n'
        'repeat each item in carrying\n    names (names & item.name & " ")\nrepeat: done\nshow names\n')
check("find single-match filters", run(FIND, seed_items), "lantern sword ")

def seed_cache(db):
    db.conn.execute("CREATE TABLE test_cache (command TEXT, room TEXT, response TEXT, use_count INTEGER)")
    db.conn.executemany("INSERT INTO test_cache VALUES (?,?,?,?)",
        [('look','kitchen','You are in the kitchen.',0),
         ('look','cellar','You are in the cellar.',0)]); db.conn.commit()
C='connect db as sqlite from env.DATABASE_URL\n'
check("retrieve multi-match (2 fields)",
      run(C+'retrieve hit from db.test_cache\n    match command to "look"\n    match room to "cellar"\nretrieve: done\nshow hit.response\n', seed_cache),
      "You are in the cellar.")

def seed_then_update(db):
    seed_cache(db)
# update multi-match then verify surgical
UPD = (C+'update db.test_cache\n    match command to "look"\n    match room to "cellar"\n    use_count 99\nupdate: done\n'
       'retrieve c from db.test_cache\n    match command to "look"\n    match room to "cellar"\nretrieve: done\nshow c.use_count\n')
check("update multi-match hits right row", run(UPD, seed_cache), 99)

print(f"\nRESULTS: {passed}/{passed+failed} passed" + ("" if not failed else f" — {failed} FAILED"))
sys.exit(1 if failed else 0)
