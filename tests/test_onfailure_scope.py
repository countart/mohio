#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for the on.failure / on.success handler-body scope fix.

Root cause that was fixed: the transformer's on_failure_handler mistook the
first nested statement (e.g. a retrieve) for the optional inline_action and
dropped it from the handler body, so a retrieve nested inside on.failure
vanished from the AST and never bound its variable. Zork's "try the room,
then inventory on failure" examine/read pattern depended on this.

Covers:
  1. A retrieve nested inside on.failure binds its variable in the OUTER scope
     (item found only in inventory after the room match fails).
  2. The inline `on.failure give back ...` form is unchanged (item absent).
  3. on.success parity: a nested retrieve inside on.success also binds.
  4. The nested inner retrieve survives in the AST (handler body is non-empty).
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
from mohio_interpreter import MohioInterpreter, DbRuntime
from mohio_ast import RetrieveBlock, OnFailure

_raw = Path('mohio.lark').read_text()
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
        '    on.failure\n'
        '        retrieve readable from db.items\n'
        '            match name to noun\n'
        '            match location to "inventory"\n'
        '            on.failure give back 200 "nope"\n'
        '        retrieve: done\n'
        'retrieve: done\n'
        'show readable.description\n')

# 1. nested retrieve inside on.failure binds in the outer scope
it = run(NEST, fresh("inventory"))
check("nested on.failure retrieve binds in outer scope", it.shown == ['THE TEXT'])

# 2. inline on.failure give-back unchanged (item absent -> the fallback fires)
it2 = fresh("attic")
tree = transform(P.parse(NEST), NEST); it2.run_declarations(tree)
res = it2.run(tree)
def _body(r):
    if isinstance(r, dict): return r.get('body')
    return getattr(r, 'value', r)
check("inline on.failure give-back still fires when item absent",
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

# 4. AST: the outer on.failure handler body is non-empty (inner retrieve kept)
prog = transform(P.parse(NEST), NEST)
outer = next(s for s in prog.statements if isinstance(s, RetrieveBlock))
of = next(h for h in outer.handlers if isinstance(h, OnFailure))
check("inner retrieve survives in the on.failure AST body",
      any(isinstance(b, RetrieveBlock) for b in of.body))

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
