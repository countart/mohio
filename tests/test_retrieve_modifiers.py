# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_retrieve_modifiers.py — retrieve.all / .every / .count / .first / .last

Covers the full wiring of the multi-row retrieve modifiers:
  - DB adapter:   retrieve_all_spec (spec-based, empty spec = every row)
  - Transformer:  retrieve_mod_block produces RetrieveBlock with .modifier set,
                  and accepts BOTH `retrieve: done` and `retrieve.all: done` closers
  - Interpreter:  _exec_RetrieveBlock branches on modifier
                  (.all/.every -> list, .count -> number, .first/.last -> row)

Run: python3 tests/test_retrieve_modifiers.py
"""
import os, sys, re
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime
from mohio_ast import RetrieveBlock

_passed = 0
_failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}: got {got!r} want {want!r}")

# ── parser (shared) ──────────────────────────────────────────
_raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')


# ─────────────────────────────────────────────────────────────
# 1. DB adapter: retrieve_all_spec
# ─────────────────────────────────────────────────────────────
print("retrieve_all_spec (DB adapter)")
db = DbRuntime(':memory:')
for nm, zn in [('hall', 'north'), ('den', 'north'), ('lab', 'south')]:
    db.save('rooms', {'name': nm, 'zone': zn})

allrows = db.retrieve_all_spec('rooms', [])                       # empty spec = every row
check("empty spec returns every row", len(allrows), 3)
check("rows carry field values", sorted(r['name'] for r in allrows), ['den', 'hall', 'lab'])

north = db.retrieve_all_spec('rooms', [('and', [('zone', 'north')])])
check("filtered spec (zone=north)", sorted(r['name'] for r in north), ['den', 'hall'])

none = db.retrieve_all_spec('rooms', [('and', [('zone', 'void')])])
check("no-match returns empty list (not None)", none, [])


# ─────────────────────────────────────────────────────────────
# 2. Transformer: modifier extraction + forgiving closer
# ─────────────────────────────────────────────────────────────
print("transformer (modifier + closer forgiveness)")
HEAD = ('connect db as sqlite\n    from env.DATABASE_URL\nconnect: done\n'
        'shape Page\n    method GET\nshape: done\n')

def _first_retrieve(prog):
    found = []
    def walk(n):
        if isinstance(n, RetrieveBlock):
            found.append(n)
        for attr in ('statements', 'body', 'listeners', 'handlers'):
            v = getattr(n, attr, None)
            if isinstance(v, list):
                for x in v: walk(x)
    walk(prog)
    return found[0] if found else None

def _build(retrieve_line, closer):
    src = (HEAD + 'listen for\n'
           '    request for sh.Page at /p\n'
           f'        {retrieve_line}\n'
           '            on.success\n                show "ok"\n'
           f'        {closer}\n'
           '        render\n            <p>ok</p>\n        render: done\n'
           '    request: done\nlisten: done\n')
    return transform(_P.parse(src), src)

for mod in ('all', 'every', 'count', 'first', 'last', 'one'):
    line = f'retrieve.{mod} x from db.rooms' + (' match id to 1' if mod in ('one',) else '')
    # .one needs a match clause; put it on the body line instead for cleanliness
    if mod == 'one':
        src = (HEAD + 'listen for\n    request for sh.Page at /p\n'
               '        retrieve.one x from db.rooms\n            match id to 1\n'
               '            on.success\n                show "ok"\n'
               '        retrieve: done\n        render\n            <p>ok</p>\n        render: done\n'
               '    request: done\nlisten: done\n')
        prog = transform(_P.parse(src), src)
    else:
        prog = _build(f'retrieve.{mod} x from db.rooms', 'retrieve: done')
    node = _first_retrieve(prog)
    check(f"retrieve.{mod} -> modifier={mod}", node.modifier if node else None, mod)

# forgiving closer: retrieve.all: done is accepted (normalized to base)
try:
    prog = _build('retrieve.all x from db.rooms', 'retrieve.all: done')
    node = _first_retrieve(prog)
    check("closer 'retrieve.all: done' accepted", node.modifier if node else None, 'all')
except Exception as e:
    check("closer 'retrieve.all: done' accepted", f"raised {type(e).__name__}", 'all')


# ─────────────────────────────────────────────────────────────
# 3. Interpreter end-to-end (single endpoint, no routing ambiguity)
# ─────────────────────────────────────────────────────────────
print("interpreter end-to-end")
SEED = ('connect db as sqlite\n    from env.DATABASE_URL\nconnect: done\n'
        'shape Room\n    name as text required\n    zone as text required\nshape: done\n'
        'shape Page\n    method GET\nshape: done\n')
ADD = ('    new sh.Room at /add\n        save to db.rooms\n            name room.name\n            zone room.zone\n'
       '        save: done\n        give back 201 "ok"\n    new: done\n')

def _run_single(retrieve_block_lines, dbfile):
    if os.path.exists(dbfile):
        os.remove(dbfile)
    os.environ['DATABASE_URL'] = dbfile
    src = (SEED + 'listen for\n' + ADD +
           '    request for sh.Page at /p\n' + retrieve_block_lines +
           '        render\n            <p>[{{ OUT }}]</p>\n        render: done\n'
           '    request: done\nlisten: done\n')
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    for nm, zn in [('hall', 'north'), ('den', 'north'), ('lab', 'south')]:
        it.run(prog, {'_method': 'POST', '_path': '/add', 'room': {'name': nm, 'zone': zn}})
    body = str(it.run(prog, {'_method': 'GET', '_path': '/p'}).get('body', ''))
    m = re.search(r'\[(.*?)\]', body)
    return m.group(1) if m else body[:60]

count_block = ('        retrieve.count OUT from db.rooms\n            on.success\n                show "ok"\n'
               '        retrieve.count: done\n')
check("retrieve.count -> 3", _run_single(count_block, '/tmp/_rm_count.db'), '3')

allc_block = ('        retrieve.all rs from db.rooms\n            on.success\n                show "ok"\n'
              '        retrieve.all: done\n').replace('{{ OUT }}', '{{ rs.count }}')
# build with rs.count in the render
def _run_collection(dbfile):
    if os.path.exists(dbfile):
        os.remove(dbfile)
    os.environ['DATABASE_URL'] = dbfile
    src = (SEED + 'listen for\n' + ADD +
           '    request for sh.Page at /p\n'
           '        retrieve.all rs from db.rooms\n            on.success\n                show "ok"\n'
           '        retrieve.all: done\n'
           '        render\n            <p>[{{ rs.count }}]</p>\n        render: done\n'
           '    request: done\nlisten: done\n')
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    for nm, zn in [('hall', 'north'), ('den', 'north'), ('lab', 'south')]:
        it.run(prog, {'_method': 'POST', '_path': '/add', 'room': {'name': nm, 'zone': zn}})
    body = str(it.run(prog, {'_method': 'GET', '_path': '/p'}).get('body', ''))
    m = re.search(r'\[(.*?)\]', body)
    return m.group(1) if m else body[:60]
check("retrieve.all collection .count -> 3", _run_collection('/tmp/_rm_all.db'), '3')

first_block = ('        retrieve.first OUT from db.rooms\n            on.success\n                show "ok"\n'
               '        retrieve.first: done\n')
# .first binds a row; render its .name
def _run_first(dbfile):
    if os.path.exists(dbfile):
        os.remove(dbfile)
    os.environ['DATABASE_URL'] = dbfile
    src = (SEED + 'listen for\n' + ADD +
           '    request for sh.Page at /p\n'
           '        retrieve.first r from db.rooms\n            on.success\n                show "ok"\n'
           '        retrieve.first: done\n'
           '        render\n            <p>[{{ r.name }}]</p>\n        render: done\n'
           '    request: done\nlisten: done\n')
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    for nm, zn in [('hall', 'north'), ('den', 'north'), ('lab', 'south')]:
        it.run(prog, {'_method': 'POST', '_path': '/add', 'room': {'name': nm, 'zone': zn}})
    body = str(it.run(prog, {'_method': 'GET', '_path': '/p'}).get('body', ''))
    m = re.search(r'\[(.*?)\]', body)
    return m.group(1) if m else body[:60]
check("retrieve.first -> first row name", _run_first('/tmp/_rm_first.db'), 'hall')

# cleanup
for f in ('/tmp/_rm_count.db', '/tmp/_rm_all.db', '/tmp/_rm_first.db'):
    if os.path.exists(f):
        os.remove(f)


# ─────────────────────────────────────────────────────────────
# 5. Invalid modifier fails loud (RETRIEVE_MOD is now /retrieve\.\w+/, so an
#    invalid modifier is caught as one token in the transformer instead of
#    leaking to the generic dotted service-call path and parsing silently).
# ─────────────────────────────────────────────────────────────
print("invalid modifier fails loud")
from mohio_transformer_ast import MohioCompileError
def _build(src):
    return transform(_P.parse(src), src)
def _expect_compile_error(label, src):
    global _passed, _failed
    try:
        _build(src)
        _failed += 1; print(f"  FAIL {label}: parsed silently (no error)")
    except MohioCompileError:
        _passed += 1; print(f"  ok   {label}")
    except Exception as e:
        _failed += 1; print(f"  FAIL {label}: wrong error {type(e).__name__}: {e}")

_expect_compile_error("retrieve.bogus errors",
    'retrieve.bogus r from db.rooms\n    match id to 1\nretrieve.bogus: done')
_expect_compile_error("retrieve.allx errors",
    'retrieve.allx r from db.rooms\n    match id to 1\nretrieve.allx: done')

# valid modifiers and plain retrieve still build (no false positives)
for m in ['one', 'first', 'last', 'all', 'every', 'count']:
    try:
        _build(f'retrieve.{m} r from db.rooms\n    match id to 1\nretrieve.{m}: done')
        _passed += 1; print(f"  ok   retrieve.{m} still builds")
    except Exception as e:
        _failed += 1; print(f"  FAIL retrieve.{m} should build: {e}")

# ─────────────────────────────────────────────────────────────
# 6. retrieve.all parses unambiguously (the regex terminal must not reintroduce
#    Earley ambiguity vs the generic dotted path on an already super-linear parse).
# ─────────────────────────────────────────────────────────────
print("retrieve.all parses to a single unambiguous tree")
_tree = _P.parse('retrieve.all r from db.rooms\n    match id to 1\nretrieve.all: done')
_has_ambig = any(getattr(t, 'data', None) == '_ambig' for t in _tree.iter_subtrees())
check("no _ambig node in retrieve.all parse", _has_ambig, False)


print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
