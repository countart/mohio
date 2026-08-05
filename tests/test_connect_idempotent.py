# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_connect_idempotent.py — connect declaration must not wipe an
already-established (and possibly seeded) database.

Regression guard for the bug where _exec_ConnectDecl re-created self._db on
every run when the driver was postgres/mysql, destroying seed data loaded via
setup_test_db / run_declarations + seed_db, and needlessly re-opening
connections on the CLI's double-connect.

Run: python3 tests/test_connect_idempotent.py
"""
import os, sys, re
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def _mk(src): return transform(_P.parse(src), src)
def _body(resp):
    m = re.search(r'\[(.*?)\]', str(resp.get('body', '')))
    return m.group(1) if m else str(resp.get('body', ''))[:60]


# ─────────────────────────────────────────────────────────────
# 1. THE BUG: a postgres connect decl + setup_test_db seed.
#    With no DATABASE_URL, postgres falls back to SQLite; the
#    seed must survive the connect decl that re-runs inside run().
# ─────────────────────────────────────────────────────────────
print("seed survives a postgres connect declaration")
os.environ.pop('DATABASE_URL', None)
pg = _mk(
    'connect db as postgres\n    from env.DATABASE_URL\nconnect: done\n'
    'shape Page\n    method GET\nshape: done\n'
    'listen for\n    request for sh.Page at /m\n'
    '        retrieve.one member from db.members\n            match id to "M001"\n'
    '            on.success\n                show "ok"\n            on.failure\n                show "missing"\n'
    '        retrieve: done\n        render\n            <p>[{{ member.name }}]</p>\n        render: done\n'
    '    request: done\nlisten: done\n')
it1 = MohioInterpreter(ai=MockAI())
it1.setup_test_db(seed_data={'members': [{'id': 'M001', 'name': 'Alice'}]})
check("seeded member resolves after run()", _body(it1.run(pg, {'_method': 'GET', '_path': '/m'})), 'Alice')
# same db object is reused, not replaced
db_before = id(it1._db)
it1.run(pg, {'_method': 'GET', '_path': '/m'})
check("db object reused across runs (not re-created)", id(it1._db), db_before)


# ─────────────────────────────────────────────────────────────
# 2. plain sqlite program: data written persists across separate
#    run() calls (the CLI double-connect analog).
# ─────────────────────────────────────────────────────────────
print("sqlite data persists across runs")
os.environ['DATABASE_URL'] = '/tmp/_ci_sqlite.db'
if os.path.exists('/tmp/_ci_sqlite.db'):
    os.remove('/tmp/_ci_sqlite.db')
sq = _mk(
    'connect db as sqlite\n    from env.DATABASE_URL\nconnect: done\n'
    'shape Room\n    name as text required\nshape: done\n'
    'shape Page\n    method GET\nshape: done\n'
    'listen for\n    new sh.Room at /add\n        save to db.rooms\n            name room.name\n'
    '        save: done\n        give back 201 "ok"\n    new: done\n'
    '    request for sh.Page at /c\n        retrieve.count n from db.rooms\n            on.success\n                show "ok"\n'
    '        retrieve.count: done\n        render\n            <p>[{{ n }}]</p>\n        render: done\n'
    '    request: done\nlisten: done\n')
it2 = MohioInterpreter(ai=MockAI())
it2.run(sq, {'_method': 'POST', '_path': '/add', 'room': {'name': 'hall'}})
it2.run(sq, {'_method': 'POST', '_path': '/add', 'room': {'name': 'den'}})
check("two writes visible on a later read", _body(it2.run(sq, {'_method': 'GET', '_path': '/c'})), '2')


# ─────────────────────────────────────────────────────────────
# 3. _connection_target resolves per driver (so a genuine target
#    change is still detected and triggers a real reconnect).
# ─────────────────────────────────────────────────────────────
print("_connection_target resolution")
it3 = MohioInterpreter(ai=MockAI())
os.environ['DATABASE_URL'] = 'postgres://host/db'
check("postgres target reads DATABASE_URL", it3._connection_target('postgres'),
      ('postgres', 'postgres://host/db'))
os.environ.pop('DATABASE_URL', None)
check("postgres target is None when unset", it3._connection_target('postgres'), ('postgres', None))
check("sqlite target is the db_path", it3._connection_target('sqlite')[0], 'sqlite')

if os.path.exists('/tmp/_ci_sqlite.db'):
    os.remove('/tmp/_ci_sqlite.db')

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
