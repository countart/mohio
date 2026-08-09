# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_mioql.py — MioQL data correctness via real HTTP path

Seeds SQLite, drives queries through routes, asserts returned data.
Covers manifest §8: retrieve modifiers, find with where/match/order, check.

BUG: `check found in db.X` inside `request for` causes a closer mismatch.
The parser reads `found in db.X` as a value expression in the check-block
rule, consuming `check: done` as the request's closer. Workaround: wrap
check-in-db in a task. Filed for compiler chat.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_mioql.py
"""
import os, sys, re, tempfile
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, AiDecision, DbRuntime
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, name='', inputs=None, **k):
        return AiDecision(result=None, confidence=0.9, fell_back=False,
                          model='mock', inputs=inputs or {})

PRODUCTS = [
    {"id": 1, "name": "Alpha Widget",  "category": "widgets", "price": 10, "stock": 100, "tag": ""},
    {"id": 2, "name": "Beta Widget",   "category": "widgets", "price": 25, "stock": 50,  "tag": "sale"},
    {"id": 3, "name": "Gamma Gadget",  "category": "gadgets", "price": 50, "stock": 0,   "tag": "new"},
    {"id": 4, "name": "Delta Gadget",  "category": "gadgets", "price": 75, "stock": 200, "tag": "sale"},
    {"id": 5, "name": "Epsilon Tool",  "category": "tools",   "price": 15, "stock": 30,  "tag": ""},
    {"id": 6, "name": "Zeta Tool",     "category": "tools",   "price": 99, "stock": 5,   "tag": "premium"},
]

def unwrap(r):
    try:
        data = r.json()
    except Exception:
        return r.text.strip()
    msg = data.get("message", data.get("body", ""))
    if isinstance(msg, str):
        m = re.match(r"MohioValue\('(.+?)',", msg)
        if m: return m.group(1)
    return str(msg)

def run_one(src_body, path="/q"):
    full = 'connect db as sqlite from env.DATABASE_URL\n\n' + src_body
    dbfile = tempfile.mktemp(suffix='.db')
    os.environ['DATABASE_URL'] = dbfile
    prog = transform(_P.parse(full), full)
    interp = MohioInterpreter(ai=MockAI(), db_path=dbfile)
    db = DbRuntime(dbfile)
    db.ensure_table("products", list(PRODUCTS[0].keys()))
    for row in PRODUCTS:
        db.save("products", row)
    interp._db = db
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(path)
    os.environ.pop('DATABASE_URL', None)
    try: os.unlink(dbfile)
    except OSError: pass
    return r.status_code, unwrap(r)


# ══════════════════════════════════════════════════════════════════════════════
# RETRIEVE MODIFIERS (directly inside request for — all work)
# ══════════════════════════════════════════════════════════════════════════════

print("\n── Retrieve modifiers ──")

for mod, vname, label, expected, give in [
    ("all",   "rows",  "count", "6",              "give back ok rows.count"),
    ("count", "total", "value", "6",              "give back ok total"),
    ("first", "row",   "name",  "Alpha Widget",   "give back ok row.name"),
    ("last",  "row",   "name",  "Zeta Tool",      "give back ok row.name"),
]:
    src = f'''\
listen for
    request for sh.Q at /q
        retrieve.{mod} {vname} from db.products
        retrieve: done
        {give}
    request: done
listen: done
'''
    status, val = run_one(src)
    check(f"retrieve.{mod} status", status, 200)
    check(f"retrieve.{mod} {label}", val, expected)

status, val = run_one('''\
listen for
    request for sh.Q at /q
        retrieve.one row from db.products
            match name to "Gamma Gadget"
        retrieve: done
        give back ok row.category
    request: done
listen: done
''')
check("retrieve.one status", status, 200)
check("retrieve.one category", val, "gadgets")


# ══════════════════════════════════════════════════════════════════════════════
# FIND (directly inside request for — works after compiler fix)
# ══════════════════════════════════════════════════════════════════════════════

print("\n── Find with match ──")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            match category to "widgets"
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("find match status", status, 200)
check("find widgets count", val, "2")


print("\n── Where clauses ──")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            where price is above 50
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("where above 50 status", status, 200)
check("where above 50 count", val, "2")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            where name contains "Tool"
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("where contains status", status, 200)
check("where contains count", val, "2")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            where tag is empty
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("where empty status", status, 200)
check("where empty count", val, "2")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            where tag is not empty
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("where not empty status", status, 200)
check("where not empty count", val, "4")


print("\n── Order and limit ──")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            order.up by price
            up to 1
        find: done
        give back ok items.first.name
    request: done
listen: done
''')
check("cheapest status", status, 200)
check("cheapest name", val, "Alpha Widget")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            order.down by price
            up to 1
        find: done
        give back ok items.first.name
    request: done
listen: done
''')
check("most expensive status", status, 200)
check("most expensive name", val, "Zeta Tool")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        find items in db.products
            order.down by price
            up to 3
        find: done
        give back ok items.count
    request: done
listen: done
''')
check("top 3 status", status, 200)
check("top 3 count", val, "3")


# ══════════════════════════════════════════════════════════════════════════════
# CHECK EXISTENCE (closer mismatch bug — written to spec)
#
# BUG: `check found in db.products` inside `request for` triggers closer
# mismatch. The parser reads `found in db.products` as a value expression
# in the check-value-block rule, then `check: done` is grabbed by request.
# Exact failing snippet provided to compiler chat.
# ══════════════════════════════════════════════════════════════════════════════

print("\n── Check existence (written to spec — closer mismatch RED) ──")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        check found in db.products
            match name to "Alpha Widget"
        check: done
        give back ok found
    request: done
listen: done
''')
check("exists status", status, 200)
check("Alpha Widget exists (True)", val, "True")

status, val = run_one('''\
listen for
    request for sh.Q at /q
        check found in db.products
            match name to "Unicorn"
        check: done
        give back ok found
    request: done
listen: done
''')
check("not exists status", status, 200)
check("Unicorn not exists (False)", val, "False")


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
