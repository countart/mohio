#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Lock tests for the four bugs surfaced while building promo snippets:

  Bug 1  each over a hold list iterates all items (no Closer crash).
  Bug 2  `check X is below/above N` parses and behaves like the bare form.
  Bug 3a `show` inside a loop surfaces EVERY iteration (output buffer).
  Bug 3b `when "0"` matches a numeric 0 (loose equality), without breaking
         exact string matching.
  Bug 4  `loop N times` produces a clear "use repeat" hint.
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


def run(src, db=None):
    it = MohioInterpreter()
    if db is not None:
        it._db = db
    tree = transform(P.parse(src), src)
    it.run_declarations(tree)
    it.run(tree)
    return it.shown


PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


# Bug 1 + 3a: each over a list shows every item
shown = run('create list backpack\n    "lantern"\n    "rope"\n    "sword"\ncreate: done\n'
            'each item in backpack\n    show item\neach: done\n')
check("Bug1/3a: each over hold list emits all items in order",
      shown == ['lantern', 'rope', 'sword'])

# Bug 3a: show inside repeat surfaces every iteration
shown = run('repeat 3 times\n    show "hi"\nrepeat: done\n')
check("Bug3a: show inside repeat emits every iteration", shown == ['hi', 'hi', 'hi'])

# Bug 3b: when "0" matches a numeric 0 (DB INTEGER column)
db = DbRuntime(':memory:')
db.conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, stock INTEGER)")
db.conn.executemany("INSERT INTO products(name,stock) VALUES (?,?)",
                    [('Lantern', 3), ('Rope', 0), ('Sword', 12)])
db.conn.commit()
shown = run('connect db as sqlite from env.DATABASE_URL\n'
            'find products in db.products\nfind: done\n'
            'each product in products\n'
            '    check product.stock\n'
            '        when "0"\n'
            '            show product.name & " - OUT OF STOCK"\n'
            '        otherwise\n'
            '            show product.name & " ok"\n'
            '    check: done\neach: done\n', db=db)
check("Bug3b: when \"0\" matches numeric 0",
      'Rope - OUT OF STOCK' in shown and 'Lantern ok' in shown and 'Sword ok' in shown)

# Bug 3b guard: exact string matching still works and does NOT over-match
shown = run('check "active"\n    when "active"\n        show "yes"\n'
            '    otherwise\n        show "no"\ncheck: done\n')
check("Bug3b guard: exact string when still matches", shown == ['yes'])
shown = run('check "12"\n    when "1"\n        show "wrong"\n'
            '    otherwise\n        show "right"\ncheck: done\n')
check("Bug3b guard: \"12\" does NOT match \"1\" (string-vs-string exact)",
      shown == ['right'])

# Bug 2: check X is below / is above parses and behaves like the bare form
def cmp_branch(subj, op):
    return run(f'check {subj}\n    {op} 18\n        show "matched"\n'
               f'    otherwise\n        show "no"\ncheck: done\n')
check("Bug2: check 15 is below 18 -> matched", cmp_branch('15', 'is below') == ['matched'])
check("Bug2: check 20 is below 18 -> no",      cmp_branch('20', 'is below') == ['no'])
check("Bug2: check 20 is above 18 -> matched", cmp_branch('20', 'is above') == ['matched'])
check("Bug2: bare 'below' still works",        cmp_branch('15', 'below') == ['matched'])

# Bug 4: loop N times -> clear repeat hint
from mio import _beginner_parse_hint
hint = _beginner_parse_hint(Exception("x"), 'loop 3 times\n    show "hi"\nloop: done\n', 2)
check("Bug4: loop N times yields a 'use repeat' hint",
      hint is not None and 'repeat' in hint.lower())
# control: a normal program yields no loop hint
check("Bug4 guard: clean source yields no loop hint",
      _beginner_parse_hint(Exception("x"), 'repeat 3 times\n    show "hi"\nrepeat: done\n', 1) is None)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
