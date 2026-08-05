# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Typed declaration followed by a dotted-value assignment + currency persistence pattern.

Two things locked here:

1. A standalone typed declaration (`p as USD`, `x as int`, ...) followed on the next line by an
   assignment whose value is a DOTTED name (`p a.price`, `x rec.field`) parses as two clean
   statements. It used to mis-parse as the retired `p as USD a.price` type-before-value form,
   because the grammar is newline-insensitive and the retired production greedily absorbed the
   next line's dotted value. The retired form still fails loud for a genuine same-line
   `x as int 5` / `x as text "hi"`.

2. Currency persistence: the `_currency` display tag is in-memory only. A currency value stored in
   a database comes back as a bare number (databases store numbers, not display metadata). To
   render it as money again, re-apply the currency contract by assigning the retrieved value to a
   currency-typed variable (`shown as USD` / `shown a.price`) or a shape field `as USD`. This is
   the intended data-layer vs presentation-layer split, and this test documents it.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime

_raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def parses(src):
    try:
        transform(P.parse(src), src); return True
    except Exception:
        return False


def run_db(src, seed):
    it = MohioInterpreter()
    db = DbRuntime(':memory:'); seed(db); it._db = db
    t = transform(P.parse(src), src); it.run_declarations(t); r = it.run(t)
    r = getattr(r, 'value', r)
    return r.get('body') if isinstance(r, dict) else r


# ── typed-decl then dotted-value assignment parses as two statements ──────────────────
check("`p as USD` then `p a.price` parses", parses('p as USD\np a.price\n'))
check("`x as int` then `x rec.field` parses", parses('x as int\nx rec.field\n'))
check("`amount as dec.2` then `amount row.total` parses", parses('amount as dec.2\namount row.total\n'))
check("`p as USD` then `p 19.99` still parses", parses('p as USD\np 19.99\n'))

# ── the retired same-line type-before-value still fails loud ───────────────────────────
check("`x as int 5` still fails loud", not parses('x as int 5\n'))
check("`x as text \"hi\"` still fails loud", not parses('x as text "hi"\n'))
check("`price as USD 19.99` still fails loud", not parses('price as USD 19.99\n'))

# ── currency persistence: reformat a retrieved bare number by re-applying the contract ─
def seed(db):
    db.conn.execute("CREATE TABLE assets (name TEXT, price REAL)")
    db.conn.execute("INSERT INTO assets VALUES ('House', 250000.5)")
    db.conn.commit()

body = run_db(
    'connect db as sqlite from env.DATABASE_URL\n'
    'retrieve a from db.assets\n    match name to "House"\nretrieve: done\n'
    'shown as USD\nshown a.price\n'
    'give back 200 ("Price " & shown)\n', seed)
check("retrieved value reformats as currency once re-typed", body == "Price $250,000.50",
      f"got {body!r}")

# raw retrieved value (no re-typing) is the plain number -- data layer, as intended
body2 = run_db(
    'connect db as sqlite from env.DATABASE_URL\n'
    'retrieve a from db.assets\n    match name to "House"\nretrieve: done\n'
    'give back 200 ("Price " & a.price)\n', seed)
check("retrieved value without re-typing is the raw number", body2 == "Price 250000.5",
      f"got {body2!r}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
