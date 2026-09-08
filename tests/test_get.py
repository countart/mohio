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
  5. a miss binds a testable absence (VALUE path) and displays as empty, never Python's
     `None` (DISPLAY path) — two separate assertions; see the supersession note at the case
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

# 5. miss binds an absence the program can test -- and displays as empty, not `None`
#
# SUPERSEDED 2026-08-23 -> 2026-08-24 (T1-EMPTY-DISPLAY-NONE). This case used to read
#     run('... get: done\nshow u\n') == [None]
# which claims to test what `get` BINDS but actually asserts what `show` RENDERS: `run()`
# returns `it.shown`, and `_exec_ShowStmt` appends `self._display_value(val)`, not `val`
# (mohio_interpreter.py:12544-12545). So the old assertion locked Python's `None` leaking
# into user-visible output -- exactly the leak T1-EMPTY-DISPLAY-NONE deliberately closed by
# rendering an empty value as Mohio's empty form. The guard lives inside `_display_value`
# alone, and `_exec_ShowStmt` still `return val` unchanged, so the BOUND value never passed
# through it. The behaviour under test did not regress; the assertion was reading the wrong
# channel and went red the moment display was corrected.
#
# The two behaviours are separate and both are locked here, one assertion each, so neither can
# be fixed by breaking the other:
#   (a) VALUE path  -- a miss binds a real absence the program can branch on (`when u is empty`)
#   (b) DISPLAY path -- showing that absence renders empty, never the host language's `None`
check("a miss binds a testable absence -- the VALUE path (when u is empty fires)",
      run('get u from db.users\n    match id to 999\nget: done\n'
          'check u\n    when u is empty\n        show "ABSENT"\n'
          '    otherwise\n        show "PRESENT"\ncheck: done\n') == ["ABSENT"])
check("showing that absence renders EMPTY, never `None` -- the DISPLAY path",
      run('get u from db.users\n    match id to 999\nget: done\nshow u\n') == [''])
check("a found record is NOT treated as absent (mutation guard on the pair above)",
      run('get u from db.users\n    match id to 1\nget: done\n'
          'check u\n    when u is empty\n        show "ABSENT"\n'
          '    otherwise\n        show "PRESENT"\ncheck: done\n') == ["PRESENT"])

# LABELLED UNIT COMPANION, paired with the real-`.mho`-path cases above (T1-TEST-REAL-PATH-
# STANDARD). It exists because the null-vs-empty-text distinction is NOT observable from Mohio
# source: `when u is empty` fires for BOTH `MohioValue(None, 'null')` and `MohioValue('',
# 'text')`, and there is no `.type` accessor to tell them apart. Proven by mutation -- swapping
# the miss binding to `MohioValue('', 'text')` left all thirteen language-level cases green.
# So the "a miss binds NULL, not an empty string" guarantee can only be locked by reading the
# bound value itself. Same executor and dispatch as a real run, with the context supplied
# explicitly (the convention tests/test_cast_canon.py already uses).
def bound(prog, var):
    from mohio_interpreter import Context
    it = fresh()
    t = transform(P.parse(H + prog), H + prog)
    ctx = Context()
    it.run_declarations(t)
    for st in t.statements:
        it._exec(st, ctx)
    return ctx.get(var)

_miss = bound('get u from db.users\n    match id to 999\nget: done\n', 'u')
check("a miss binds NULL specifically -- not an empty string (the VALUE path, exactly)",
      getattr(_miss, 'to_python', lambda: _miss)() is None)
_hit = bound('get u from db.users\n    match id to 1\nget: done\n', 'u')
check("a hit binds the real record (mutation guard on the null check above)",
      getattr(_hit, 'to_python', lambda: _hit)() is not None)

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
