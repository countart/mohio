#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Lock tests for multi-match retrieve / update — every `match` clause is honored
and AND-ed together (the bug that made Zork's item lookup ignore `location`).

Covers:
  1. retrieve with two match clauses binds the row matching BOTH.
  2. retrieve with a single match clause is unchanged (back-compat).
  3. update with two match clauses touches ONLY rows matching BOTH.
  4. backend retrieve_one_multi ANDs its conditions.
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


def fresh():
    db = DbRuntime(':memory:')
    db.conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, "
                    "location TEXT, description TEXT)")
    db.conn.executemany(
        "INSERT INTO items(name,location,description) VALUES (?,?,?)",
        [('leaflet', 'mailbox',   'WRONG copy'),
         ('leaflet', 'inventory', 'RIGHT copy'),
         ('lamp',    'inventory', 'a brass lamp')])
    db.conn.commit()
    return db


def run(src, db):
    it = MohioInterpreter(); it._db = db
    tree = transform(P.parse(src), src)
    it.run_declarations(tree); it.run(tree)
    return it.shown


PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


# 1. two match clauses -> binds the row matching BOTH (name AND location)
shown = run(H + 'hold noun = "leaflet"\nhold room = "inventory"\n'
            'retrieve seen from db.items\n'
            '    match name to noun\n'
            '    match location to room\n'
            'retrieve: done\nshow seen.description\n', fresh())
check("retrieve: two matches AND-ed -> RIGHT copy", shown == ['RIGHT copy'])

# 2. single match clause -> unchanged (binds first match)
shown = run(H + 'hold noun = "leaflet"\n'
            'retrieve seen from db.items\n'
            '    match name to noun\n'
            'retrieve: done\nshow seen.description\n', fresh())
check("retrieve: single match unchanged -> first (WRONG copy)", shown == ['WRONG copy'])

# 3. update with two match clauses touches only the row matching BOTH
db = fresh()
run(H + 'hold noun = "leaflet"\nhold room = "inventory"\n'
    'update db.items\n'
    '    match name to noun\n'
    '    match location to room\n'
    '    description "TOUCHED"\n'
    'update: done\n', db)
rows = {r[0]: r[1] for r in db.conn.execute(
    "SELECT location, description FROM items WHERE name='leaflet'").fetchall()}
check("update: two matches AND-ed -> only inventory row changed",
      rows.get('inventory') == 'TOUCHED' and rows.get('mailbox') == 'WRONG copy')

# 4. backend retrieve_one_multi ANDs conditions
db = fresh()
row = db.retrieve_one_multi('items', {'name': 'leaflet', 'location': 'inventory'})
check("backend retrieve_one_multi ANDs conditions",
      row is not None and row.get('description') == 'RIGHT copy')
row = db.retrieve_one_multi('items', {'name': 'leaflet', 'location': 'nowhere'})
check("backend retrieve_one_multi returns None when no row matches both", row is None)

# 5. comma syntax — all three forms produce the same AND-ed result as stacked
def desc(src_body, db):
    return run(H + 'hold noun = "leaflet"\nhold room = "inventory"\n' + src_body +
               'show seen.description\n', db)

# D: inline comma match
check("comma D: inline `match a to x, b to y` -> AND-ed (RIGHT copy)",
      desc('retrieve seen from db.items\n    match name to noun, location to room\nretrieve: done\n',
           fresh()) == ['RIGHT copy'])
# C: trailing-comma block
check("comma C: trailing-comma block -> AND-ed (RIGHT copy)",
      desc('retrieve seen from db.items\n    match name to noun,\n         location to room\nretrieve: done\n',
           fresh()) == ['RIGHT copy'])
# A: inline `where` one-liner, no closer
check("comma A: inline `where a is x, b is y` one-liner -> AND-ed (RIGHT copy)",
      desc('retrieve seen from db.items where name is noun, location is room\n',
           fresh()) == ['RIGHT copy'])
# A single condition keeps single-match behavior
check("comma A: single inline `where a is x` -> first match (back-compat)",
      desc('retrieve seen from db.items where name is noun\n', fresh()) == ['WRONG copy'])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
