# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""A shape DECLARES the type, so the request boundary is where text becomes a number.

`price as decimal` used to arrive as the text "10.50", so `(price + tax)` blew up in the
math instead of being 12.50. The shape type was declared and then ignored. Now the boundary
converts, and a value that cannot convert is a validation error (422), not a 500 buried in
an expression. Empty means zero, exactly like the `as.int` cast.

`total` is a naked (not `hold`) variable, 2026-08-04: this test's single TestClient makes 4
requests, which the session-lifecycle build now correctly carries as ONE persistent session
(mio_session is genuinely emitted and round-tripped for the first time -- previously this
test's program wrote no cookie at all, so the client never received or resent one, and every
request was silently treated as a brand-new anonymous session with a fresh context). `hold`
forbids re-declaration, so `hold total` -- wrong for a value recomputed fresh every request
regardless -- only ever worked by accident, because sessions never actually persisted here.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_SRC = '''shape Order
    price as decimal
    tax as decimal
    qty as int
shape: done
listen for
    new sh.Order at /o
        total (request.price + request.tax)
        give back ok total
    new: done
listen: done
'''
_c = TestClient(create_app(MohioServer(transform(_P.parse(_SRC), _SRC), MohioInterpreter())),
                raise_server_exceptions=False)
_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

r = _c.post('/o', json={'price': '10.50', 'tax': '2', 'qty': '1'})
check('declared decimals: "10.50" + "2" -> 12.5', r.status_code == 200 and '12.5' in r.text)
r = _c.post('/o', json={'price': '', 'tax': '2', 'qty': '1'})
check('empty declared number is zero',            r.status_code == 200 and '2' in r.text)
r = _c.post('/o', json={'price': '10', 'tax': '2', 'qty': '1'})
check('whole numbers still add',                  r.status_code == 200 and '12' in r.text)
r = _c.post('/o', json={'price': 'discounted', 'tax': '2', 'qty': '1'})
check('non-numeric is a 422, not a 500',          r.status_code == 422 and 'number' in r.text)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
