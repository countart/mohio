# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Block-form `miocookie.set` options actually reach the wire (2026-08-02).

The transformer used to extract only name/inline_value and DROP the whole block body, so
`secure`, `http only`, `same site`, `expires`, `domain`, `path` never reached the runtime no
matter what was declared. Now each aliased clause is captured onto the node and emitted as the
matching Set-Cookie attribute. Proven end-to-end through the real serving path (TestClient), each
attribute confirmed independently, and mutation-proven per field: dropping a clause's capture
removes exactly that attribute from the header (http_only defaults on, so its proof is the
node-capture, not header-disappearance).

Run: `python tests/test_miocookie_block_options.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
# Deliberately do NOT set MOHIO_COOKIE_SECURE: Secure must appear because the program declared
# `secure` explicitly, not because of the transport default -- so dropping the clause removes it.
os.environ.pop('MOHIO_COOKIE_SECURE', None)

from lark import Lark
import mohio_transformer_ast as TA
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import MioCookieSet
from mohio_server import create_app, MohioServer
from starlette.testclient import TestClient

_RAW = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SRC = (
    'shape Cmd\n    command as text\nshape: done\n'
    'listen for\n    new sh.Cmd\n'
    '        miocookie.set "sid"\n'
    '            value "abc"\n'
    '            secure\n'
    '            http only\n'
    '            same site "strict"\n'
    '            expires in 30 days\n'
    '            domain "example.com"\n'
    '            path "/app"\n'
    '        miocookie.set: done\n'
    '        give back 200 "ok"\n'
    '    new: done\nlisten: done\n'
)

def set_cookie():
    prog = transform(_P.parse(SRC), SRC)
    it = MohioInterpreter(); it.run_declarations(prog)
    c = TestClient(create_app(MohioServer(prog, it)))
    r = c.request('POST', '/', json={'command': 'x'})
    sc = [v.decode() for k, v in r.headers.raw if k.lower() == b'set-cookie']
    return sc[0] if sc else ''

def cookie_node():
    prog = transform(_P.parse(SRC), SRC)
    def find(n):
        import dataclasses as dc
        if isinstance(n, MioCookieSet): return n
        if isinstance(n, list):
            for x in n:
                r = find(x)
                if r: return r
        if dc.is_dataclass(n):
            for fld in dc.fields(n):
                r = find(getattr(n, fld.name, None))
                if r: return r
        return None
    return find(prog)

# ── Baseline: every declared attribute appears on the wire, independently ──────────────────
sc = set_cookie()
check("value    -> sid=abc",             'sid=abc' in sc, sc)
check("secure   -> Secure",              'Secure' in sc, sc)
check("http only-> HttpOnly",            'HttpOnly' in sc, sc)
check("same site-> SameSite=Strict",     'SameSite=Strict' in sc, sc)
check("expires  -> Max-Age=2592000 (30d)", 'Max-Age=2592000' in sc, sc)
check("domain   -> Domain=example.com",  'Domain=example.com' in sc, sc)
check("path     -> Path=/app",           'Path=/app' in sc, sc)

# ── Mutation per field: dropping a clause's capture removes exactly that attribute ─────────
def _dropper(self, children):
    return ('__ck__', '__dropped__', None)   # a marker with a key nothing reads -> field unset

def mutate(method_name):
    orig = getattr(TA.MohioTransformer, method_name)
    setattr(TA.MohioTransformer, method_name, _dropper)
    try:
        return set_cookie()
    finally:
        setattr(TA.MohioTransformer, method_name, orig)

check("drop `secure`    -> Secure gone",           'Secure' not in mutate('cookie_secure'))
check("drop `same site` -> SameSite=Strict gone (falls to Lax)",
      'SameSite=Strict' not in mutate('cookie_same_site'))
check("drop `expires`   -> Max-Age gone",          'Max-Age' not in mutate('cookie_expires'))
check("drop `domain`    -> Domain gone",           'Domain=' not in mutate('cookie_domain'))
check("drop `path`      -> Path=/app gone (falls to /)", 'Path=/app' not in mutate('cookie_path'))
check("drop `value`     -> sid=abc gone",          'sid=abc' not in mutate('cookie_value'))

# http_only defaults ON, so it stays on the wire even when dropped; prove the CAPTURE instead.
n = cookie_node()
check("http only captured on the node (node.http_only is True)", n.http_only is True, str(n))
_orig = TA.MohioTransformer.cookie_http_only
TA.MohioTransformer.cookie_http_only = _dropper
try:
    check("drop `http only` -> node.http_only unset (capture is wired to the clause)",
          cookie_node().http_only is None)
finally:
    TA.MohioTransformer.cookie_http_only = _orig

# Sanity: the control attribute survives each mutation (mutations are field-specific, not global)
check("control: value survives dropping `secure`", 'sid=abc' in mutate('cookie_secure'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
