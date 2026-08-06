# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""grab is a distinct verb, not an alias of get.

The test of distinctness (Ronnie's rule): grab works as a quick one-liner WITHOUT a closer. If it
needed `grab: done` it would just be the block-form get under another name. It has its own inline
form `grab x from t where field to value` (no closer), plus the block form for parity.
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
    db.conn.execute("CREATE TABLE members (id INTEGER, name TEXT)")
    db.conn.execute("INSERT INTO members VALUES (5,'Aria')")
    db.conn.execute("INSERT INTO members VALUES (6,'Bo')")
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


CONN = 'connect db as sqlite from env.DATABASE_URL\n'

# ── the distinctness test: grab works WITHOUT a closer ────────────────────────────────
check("grab one-liner (no closer) binds a record",
      run(CONN + 'grab m from db.members where id is 5\ngive back 200 m.name\n') == "Aria")
check("grab one-liner parses without `grab: done`",
      parses('connect db as sqlite from env.DATABASE_URL\n'
             'grab m from db.members where id is 5\ngive back 200 m.name\n'))

# ── the block form still works (parity) ───────────────────────────────────────────────
check("grab block form (with closer) still binds a record",
      run(CONN + 'grab m from db.members\n    match id to 5\ngrab: done\n'
                 'give back 200 m.name\n') == "Aria")

# ── grab picks the right record by its where condition ────────────────────────────────
check("grab one-liner filters correctly",
      run(CONN + 'grab m from db.members where id is 6\ngive back 200 m.name\n') == "Bo")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
