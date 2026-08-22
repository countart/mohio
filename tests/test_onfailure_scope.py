#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Lock tests for the on.failure / on.success handler-body scope fix.

Root cause that was fixed: the transformer's on_failure_handler mistook the
first nested statement (e.g. a retrieve) for the optional inline_action and
dropped it from the handler body, so a retrieve nested inside on.failure
vanished from the AST and never bound its variable. Zork's "try the room,
then inventory on failure" examine/read pattern depended on this.

Covers:
  1. A retrieve nested inside a NOT-FOUND branch binds its variable in the OUTER scope
     (item found only in inventory after the room match fails).
  2. A `give back` inside a not-found branch still fires (item absent everywhere).
  3. on.success parity: a nested retrieve inside on.success also binds.
  4. The nested inner retrieve survives in an on.failure AST body (handler body is non-empty).

SUPERSESSION (ruled 2026-08-19, recorded 2026-08-21). Cases 1 and 2 originally spelled the
NOT-FOUND branch as `on.failure`, because that is what it meant when this test was written.
FORK-1 (`c023363`, T1-EVAL-SIMPLE-FAILLOUD) and the ruling in build-diary 2026-08-19-05 moved
not-found onto `when empty` / `otherwise` and relegated `on.failure` to genuine ERRORS only:
"on.failure is NOT the found-nothing path -- it is the error path." A zero-row match no longer
fires `on.failure`, so cases 1 and 2 were asserting a meaning the language had deliberately
dropped. They now say `when readable is empty`.

Bisected rather than assumed: 4/4 passing at `1bd295d` (FORK-1's parent), 2/4 from `c023363`
onward and byte-identical since -- so this was FORK-1's ruling landing, not a later fix leaving a
gap behind it.

Only the TRIGGER is superseded; the CAPABILITY is unchanged, verified in both directions before
this edit was made. The same programs under `when ... is empty` produce the same values these
cases always asserted -- `['THE TEXT']` when the item is in inventory, `'nope'` when it is
nowhere -- so the expectations below are untouched. Only the spelling of "the row was not found"
changed.

This test's real subject is UNCHANGED and still fully locked: the transformer bug where
`on_failure_handler` mistook the first nested statement for its optional `inline_action` and
dropped it. Case 3 exercises that on `on.success` and case 4 on an `on.failure` AST directly;
neither was ever affected by the ruling, and both still assert exactly what they did.
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
from mohio_ast import RetrieveBlock, OnFailure

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'


def fresh(loc):
    db = DbRuntime(':memory:')
    db.conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, "
                    "location TEXT, description TEXT)")
    db.conn.execute("INSERT INTO items(name,location,description) "
                    f"VALUES ('leaflet','{loc}','THE TEXT')")
    db.conn.commit()
    it = MohioInterpreter(); it._db = db
    return it


def run(src, it):
    tree = transform(P.parse(src), src)
    it.run_declarations(tree); it.run(tree)
    return it


PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


NEST = (H + 'hold noun = "leaflet"\nhold current_room = "west_of_house"\n'
        'retrieve readable from db.items\n'
        '    match name to noun\n'
        '    match location to current_room\n'
        '    when readable is empty\n'
        '        retrieve readable from db.items\n'
        '            match name to noun\n'
        '            match location to "inventory"\n'
        '            when readable is empty\n'
        '                give back 200 "nope"\n'
        '        retrieve: done\n'
        'retrieve: done\n'
        'show readable.description\n')

# 1. nested retrieve inside the not-found branch binds in the outer scope
it = run(NEST, fresh("inventory"))
check("nested not-found retrieve binds in outer scope", it.shown == ['THE TEXT'])

# 2. give-back inside the not-found branch unchanged (item absent -> fallback fires)
it2 = fresh("attic")
tree = transform(P.parse(NEST), NEST); it2.run_declarations(tree)
res = it2.run(tree)
def _body(r):
    if isinstance(r, dict): return r.get('body')
    return getattr(r, 'value', r)
check("give-back in a not-found branch still fires when item absent",
      _body(res) == 'nope')

# 3. on.success parity: nested retrieve inside on.success binds
SUCC = (H + 'hold noun = "leaflet"\n'
        'retrieve first from db.items\n'
        '    match name to noun\n'
        '    on.success\n'
        '        retrieve again from db.items\n'
        '            match name to noun\n'
        '            on.failure give back 200 "nope"\n'
        '        retrieve: done\n'
        'retrieve: done\n'
        'show again.description\n')
it = run(SUCC, fresh("inventory"))
check("nested on.success retrieve binds in outer scope", it.shown == ['THE TEXT'])

# 4. AST: the on.failure handler body is non-empty (inner retrieve kept).
#
#    This case keeps its ORIGINAL subject exactly -- the transformer must not swallow the
#    first nested statement of an `on.failure` body as its optional `inline_action`. It used
#    to borrow NEST for that, but NEST now expresses not-found as `when ... is empty` (see the
#    supersession note above) and so carries no OnFailure node to inspect. The check is
#    unchanged; it simply gets its own `on.failure` program to look at. That is the right
#    home for it regardless: `on.failure` still exists for genuine errors, and the transformer
#    bug this locks is about how its BODY is parsed, not about when it fires at runtime.
AST_ONFAILURE = (H + 'hold noun = "leaflet"\n')
AST_ONFAILURE += ('retrieve readable from db.items\n')
AST_ONFAILURE += ('    match name to noun\n')
AST_ONFAILURE += ('    on.failure\n')
AST_ONFAILURE += ('        retrieve fallback from db.items\n')
AST_ONFAILURE += ('            match name to noun\n')
AST_ONFAILURE += ('        retrieve: done\n')
AST_ONFAILURE += ('retrieve: done\n')
prog = transform(P.parse(AST_ONFAILURE), AST_ONFAILURE)
outer = next(s for s in prog.statements if isinstance(s, RetrieveBlock))
of = next(h for h in outer.handlers if isinstance(h, OnFailure))
check("inner retrieve survives in the on.failure AST body",
      any(isinstance(b, RetrieveBlock) for b in of.body))

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
