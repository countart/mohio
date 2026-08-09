# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The `match ... to` vs `where ... is` distinction (the clause-word law).

Two different clause types, each with its natural English word:

  match = correspondence / mapping -> `to`   (match user.id to order.user_id)
  where = filter condition         -> `is`   (where status is "active")

`to` in a match clause is a correspondence ("map this to that"), which reads as English and stays.
`to` in a where clause was `to` masquerading as equality ("where id to 5"), which fails the walk-by
test -- a filter is a state check, so it reads with `is`. This test locks both: match keeps `to`,
where (including the inline retrieval form) uses `is`.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime

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


def _seed(db):
    db.conn.execute("CREATE TABLE items (id INTEGER, name TEXT, location TEXT, copy TEXT)")
    db.conn.executemany("INSERT INTO items VALUES (?,?,?,?)",
                        [(1, 'sword', 'armory', 'RIGHT copy'),
                         (2, 'sword', 'cellar', 'WRONG copy')])
    db.conn.commit()


def run(src):
    it = MohioInterpreter(); db = DbRuntime(':memory:'); _seed(db); it._db = db
    t = transform(P.parse(src), src); it.run_declarations(t); r = it.run(t)
    r = getattr(r, 'value', r)
    return r.get('body') if isinstance(r, dict) else r


def parses(src):
    try:
        transform(P.parse(src), src); return True
    except Exception:
        return False


C = 'connect db as sqlite from env.DATABASE_URL\n'

# ── where uses `is` (filter condition) ────────────────────────────────────────────────
check("retrieve inline `where id is 1` works",
      run(C + 'retrieve x from db.items where id is 1\ngive back 200 x.copy\n') == "RIGHT copy")
check("retrieve inline multi `where a is x, b is y` (AND-ed) works",
      run(C + 'retrieve x from db.items where name is "sword", location is "armory"\n'
              'give back 200 x.copy\n') == "RIGHT copy")
check("grab inline `where id is 1` works",
      run(C + 'grab x from db.items where id is 1\ngive back 200 x.copy\n') == "RIGHT copy")

# ── `where ... to` (to-as-equality in a filter) is no longer accepted ─────────────────
check("retrieve inline `where id to 1` is rejected (where uses is, not to)",
      not parses('connect db as sqlite from env.DATABASE_URL\n'
                 'retrieve x from db.items where id to 1\ngive back 200 x.copy\n'))

# ── `match ... to` (correspondence mapping) stays valid in block forms ─────────────────
check("retrieve block `match id to 1` still works (match keeps to)",
      run(C + 'retrieve x from db.items\n    match id to 1\nretrieve: done\n'
              'give back 200 x.copy\n') == "RIGHT copy")
check("grab block `match id to 1` still works (match keeps to)",
      run(C + 'grab x from db.items\n    match id to 1\ngrab: done\n'
              'give back 200 x.copy\n') == "RIGHT copy")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
