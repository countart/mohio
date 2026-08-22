# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-RETRIEVE-HANDLER-IN-TRY (2026-08-20): a `when`/`otherwise` branch of a `retrieve` is a full
execution context -- `give back` leaves the block, and a nested verb's own error stays its own.

THE BUG (reported from a live Zork probe, probe7.mho, cases A/B/D -- 14 RUN-4 migration sites were
blocked on it). `_exec_RetrieveBlock` wrapped its database call in `try/except Exception`, and
`_bind_and_succeed` -- which runs the program's OWN `when`/`otherwise`/`on.success` bodies -- was
called INSIDE that try. So ordinary user code in a branch executed inside an except clause meant
for driver errors. Two distinct failures fell out, both proven live on Postgres:

  1. `give back` inside a branch raises `_GiveBack`, the interpreter's own CONTROL FLOW, not an
     error. It was caught and degraded to `db_error: ` with an EMPTY detail -- `str(_GiveBack())`
     is "". A route that answered correctly reported a database failure that never happened, and
     named nothing. An empty-detail error is its own defect: it costs an investigation round trip.

  2. A NESTED retrieve inside a branch reports its own failure as `_Raise('db_error', msg)`. The
     outer try caught that too and wrapped it AGAIN -> `db_error: db_error: relation "x" does not
     exist`. The doubled prefix was this same swallow, one level up.

Why `on.success`/`on.failure` nested fine while `when`/`otherwise` did not -- the detail that
located the bug: `_handle_failure` is invoked FROM the except clause, i.e. outside the try, so
`on.failure` bodies always ran on clean footing. FORK-1 routed a legitimate miss through
`_bind_and_succeed` instead, which moved CONDITION-channel bodies inside the try. Raw SQL in a
branch kept working for the same reason -- a different path, not wrapped.

THE FIX: resolve the value inside the try, dispatch handlers after it. `_bind_and_succeed` is
unchanged and is still the one shared mechanism -- it is only called one scope further out, which
puts the CONDITION channel on the same footing the STATE channel always had. An
`except (_GiveBack, _Raise, _Stop, _Skip): raise` guard sits ahead of the broad except so the
guarantee is explicit rather than dependent on the code above staying arranged this way.

SIBLING SWEEP -- the full matrix, run live rather than reasoned about. Only `retrieve` was affected
(every modifier: .one/.all/.first/.last/.count). `find`, `grab`, `check`, `compare`, `save` and
`update` already dispatched their handlers outside their driver-error try, and were confirmed
unaffected before and after the change. Raw SQL nested in a branch was confirmed still working.

MUTATION RECORD (2026-08-20). The fix carries TWO independent protections, and that redundancy
is deliberate, so neither one alone is load-bearing:
  M1  dispatch moved back INSIDE the try, guard kept    -> test still passes (the guard covers it)
  M2  guard deleted, dispatch left outside the try      -> test still passes (nothing raises inside)
  M3  BOTH removed = the exact pre-fix arrangement      -> 7 FAILURES: probe7 cases A/B/D, both
                                                          plain `give back` cases, the doubled
                                                          prefix, and raw-SQL-in-branch.
M1 and M2 passing is the correct result, not a coverage hole -- each protection independently
prevents the bug. M3 is what proves the suite is wired to the behaviour rather than to the shape
of the code.

Run: `python tests/test_retrieve_handler_in_try.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'shape Room\n    rid as text\n    name as text\nshape: done\n'
        'shape Puzzle\n    room as text\n    pid as text\nshape: done\n'
        'save to db.rooms\n    rid "west"\n    name "West of House"\nsave: done\n'
        'save to db.puzzles\n    room "west"\n    pid "P1"\nsave: done\n')

def run(src):
    full = SEED + src
    prog = ast_transform(P.parse(full), full)
    it = MohioInterpreter(); it.run_declarations(prog)
    return it, it.run(prog)

def body(src):
    _it, r = run(src)
    return (r or {}).get('body') if isinstance(r, dict) else r


# -- probe7 case A: nested retrieve inside `otherwise`, inner branches `give back` -----------
b = body('retrieve outer from db.rooms\n    match rid to "west"\n'
         '    when outer is empty\n        give back 200 "A: outer empty"\n'
         '    otherwise\n'
         '        retrieve inner from db.puzzles\n            match room to "west"\n'
         '            when inner is empty\n                give back 200 "A: inner empty"\n'
         '            otherwise\n                give back 200 ("A: OK " & inner.pid)\n'
         '        retrieve: done\n'
         'retrieve: done\n')
check("case A: nested retrieve inside `otherwise`, give back in the inner branch",
      b == "A: OK P1", b)

# -- probe7 case B: nested retrieve inside `when ... is empty` -------------------------------
b = body('retrieve outer2 from db.rooms\n    match rid to "no_such_room_xyz"\n'
         '    when outer2 is empty\n'
         '        retrieve inner2 from db.puzzles\n            match room to "west"\n'
         '            when inner2 is empty\n                give back 200 "B: inner empty"\n'
         '            otherwise\n                give back 200 ("B: OK " & inner2.pid)\n'
         '        retrieve: done\n'
         '    otherwise\n        give back 200 "B: outer found"\n'
         'retrieve: done\n')
check("case B: nested retrieve inside `when ... is empty`", b == "B: OK P1", b)

# -- probe7 case D: nested retrieve.all inside `otherwise` -----------------------------------
b = body('retrieve o4 from db.rooms\n    match rid to "west"\n'
         '    when o4 is empty\n        give back 200 "D: outer empty"\n'
         '    otherwise\n'
         '        retrieve.all i4 from db.puzzles\n'
         '            when i4 is empty\n                give back 200 "D: inner all empty"\n'
         '            otherwise\n                give back 200 "D: OK"\n'
         '        retrieve.all: done\n'
         'retrieve: done\n')
check("case D: nested retrieve.all inside `otherwise`", b == "D: OK", b)

# -- probe7 case C (the control): sibling, NOT nested -- must still pass ---------------------
b = body('retrieve o3 from db.rooms\n    match rid to "west"\n'
         '    when o3 is empty\n        o3_skip "ok"\n'
         'retrieve: done\n'
         'retrieve i3 from db.puzzles\n    match room to "west"\n'
         '    when i3 is empty\n        i3_skip "ok"\n'
         'retrieve: done\n'
         'give back 200 "C: sibling retrieves -- OK"\n')
check("case C control: sibling (non-nested) retrieves still pass",
      b == "C: sibling retrieves -- OK", b)

# -- a bare `give back` in a branch, no nesting at all -- the simplest form of the bug -------
b = body('retrieve r from db.rooms\n    match rid to "west"\n'
         '    when r is empty\n        give back 200 "empty"\n'
         '    otherwise\n        give back 200 "give back from otherwise"\n'
         'retrieve: done\n')
check("a plain `give back` inside `otherwise` is control flow, not a db_error",
      b == "give back from otherwise", b)

b = body('retrieve r from db.rooms\n    match rid to "nope"\n'
         '    when r is empty\n        give back 200 "give back from when-empty"\n'
         'retrieve: done\n')
check("a plain `give back` inside `when ... is empty` is control flow, not a db_error",
      b == "give back from when-empty", b)


# -- MESSAGE QUALITY: a genuine driver error is still reported, ONCE, naming the situation ---
_it, r = run('retrieve r from db.rooms\n    match rid to "west"\n'
             '    when r is empty\n        show "unexpected"\n'
             '    otherwise\n'
             '        retrieve g from db.ghost_table_xyz\n            match room to "x"\n'
             '        retrieve: done\n'
             '        show "unreachable"\n'
             'retrieve: done\n')
msg = str((r or {}).get('body', ''))
check("a genuine nested driver error still FAILS LOUD (never silently swallowed)",
      (r or {}).get('status') == 500, r)
check("...the db_error prefix appears exactly ONCE (was doubled)",
      msg.count('db_error') == 1, msg)
check("...and the message names the real situation (the missing relation)",
      'ghost_table_xyz' in msg, msg)
check("...and the detail is never empty (an empty-detail error names nothing)",
      msg.replace('db_error:', '').strip() != '', repr(msg))


# -- SIBLING GUARDS: the verbs that were already correct must stay correct -------------------
b = body('find f in db.rooms\n    where rid is "west"\n'
         '    when f is empty\n        give back 200 "unexpected"\n'
         '    otherwise\n        give back 200 "find OK"\n'
         'find: done\n')
check("sibling: find -- give back in a branch still works", b == "find OK", b)

b = body('grab g from db.rooms\n    match rid to "west"\n'
         '    when g is empty\n        give back 200 "unexpected"\n'
         '    otherwise\n        give back 200 "grab OK"\n'
         'grab: done\n')
check("sibling: grab -- give back in a branch still works", b == "grab OK", b)

b = body('x 5\ncheck x\n    when 5\n        give back 200 "check OK"\n'
         '    otherwise\n        give back 200 "unexpected"\ncheck: done\n')
check("sibling: check block -- give back in a branch still works", b == "check OK", b)

b = body('a 5\nb 5\ncompare a to b\n'
         '    when comparison.equal\n        give back 200 "compare OK"\n'
         '    otherwise\n        give back 200 "unexpected"\ncompare: done\n')
check("sibling: compare -- give back in a branch still works", b == "compare OK", b)

# Raw SQL nested in a branch kept working throughout -- it never went through the wrapped path.
_RAWSQL = ('retrieve r from db.rooms\n    match rid to "west"\n'
           '    when r is empty\n        give back 200 "unexpected"\n'
           '    otherwise\n'
           '        retrieve rs from db.rooms\n            sql\n'
           "                SELECT * FROM rooms WHERE rid = 'west'\n"
           '            sql: done\n'
           '        retrieve: done\n'
           '        give back 200 "rawsql-in-branch OK"\n'
           'retrieve: done\n')
b = body(_RAWSQL)
check("sibling: raw SQL nested in a branch still works", b == "rawsql-in-branch OK", b)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
