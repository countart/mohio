# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_giveback_format.py — content-type on give back as FORMAT + quoted at-paths

Feature 1: give back ... as xml/text/html/json sets content-type and raw body
Feature 2: quoted at-paths fail loud at compile

Written to spec. Reds are bug reports.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_giveback_format.py
"""
import os, sys, re, subprocess, tempfile
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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

def check_in(label, haystack, needle):
    global _passed, _failed
    if needle.lower() in str(haystack).lower():
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: {needle!r} not in output")

_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, name='', inputs=None, **k):
        return AiDecision(result=None, confidence=0.9, fell_back=False,
                          model='mock', inputs=inputs or {})

def serve(src):
    full = 'connect db as sqlite from env.DATABASE_URL\n\n' + src
    prog = transform(_P.parse(full), full)
    it = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, it)
    return TestClient(create_app(server), raise_server_exceptions=False)

def run_check(src):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mho', dir=ROOT,
                                     delete=False, encoding='utf-8') as tmp:
        tmp.write(src); path = tmp.name
    try:
        return subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'),
                               'check', path],
                              capture_output=True, text=True, cwd=ROOT)
    finally:
        try: os.unlink(path)
        except OSError: pass

def run_fmt(src, write=False):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mho', dir=ROOT,
                                     delete=False, encoding='utf-8') as tmp:
        tmp.write(src); path = tmp.name
    try:
        args = [sys.executable, os.path.join(ROOT, 'mio.py'), 'fmt', path]
        if write:
            args.append('--write')
        r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
        content = open(path, encoding='utf-8').read() if write else None
        return r, content
    finally:
        if not write:
            try: os.unlink(path)
            except OSError: pass


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — content-type on give back as FORMAT
# ══════════════════════════════════════════════════════════════════════════════
print("\n── F1: as xml ──")

c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "<a/>" as xml
    request: done
listen: done
''')
r = c.get("/q")
check("xml status", r.status_code, 200)
ct = r.headers.get('content-type', '')
check_in("xml content-type", ct, 'application/xml')
check("xml raw body", r.text, '<a/>')

print("\n── F1: as text ──")

c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "hi" as text
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("text content-type", ct, 'text/plain')
check("text raw body", r.text, 'hi')

print("\n── F1: as json ──")

c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "x" as json
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("json content-type", ct, 'application/json')

print("\n── F1: as html ──")

c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "<h1>x</h1>" as html
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("html content-type", ct, 'text/html')

print("\n── F1: bare markup (no format) ──")

c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "<h1>x</h1>"
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("bare markup → html", ct, 'text/html')

print("\n── F1: edges ──")

# Empty body + as xml
c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "" as xml
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("empty body as xml", ct, 'application/xml')

# as text body with < (must not be sniffed as HTML)
c = serve('''\
listen for
    request for sh.Q at /q
        give back 200 "<not html just text>" as text
    request: done
listen: done
''')
r = c.get("/q")
ct = r.headers.get('content-type', '')
check_in("as text with < stays text/plain", ct, 'text/plain')
check("as text raw body", r.text, '<not html just text>')

# Numeric status other than 200 + as xml
c = serve('''\
listen for
    request for sh.Q at /q
        give back 404 "<error>not found</error>" as xml
    request: done
listen: done
''')
r = c.get("/q")
check("404 as xml status", r.status_code, 404)
ct = r.headers.get('content-type', '')
check_in("404 as xml content-type", ct, 'application/xml')

# Dynamic XML from a page (GET route)
c2 = serve('''\
journey App
    page Sitemap at /sitemap.xml
        xml "<urlset><url><loc>https://example.com</loc></url></urlset>"
        give back 200 xml as xml
    page: done
journey: done
''')
r = c2.get("/sitemap.xml")
check("dynamic xml status", r.status_code, 200)
ct = r.headers.get('content-type', '')
check_in("dynamic xml content-type", ct, 'application/xml')
check_in("dynamic xml body", r.text, '<urlset>')

# POST route with as text
c3 = serve('''\
shape Cmd
    action as text required
shape: done
listen for
    new sh.Cmd at /cmd
        give back 200 "processed" as text
    new: done
listen: done
''')
r = c3.post("/cmd", json={"_shape": "Cmd", "action": "go"})
ct = r.headers.get('content-type', '')
check_in("POST as text content-type", ct, 'text/plain')


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — quoted at-paths fail loud
# ══════════════════════════════════════════════════════════════════════════════
print("\n── F2: quoted paths fail loud ──")

QUOTED_CASES = [
    ('page at "/about"',
     'page at "/about"\n    show "hi"\npage: done\n'),
    ('request for at "/y"',
     'listen for\n    request for sh.X at "/y"\n        give back ok "hi"\n    request: done\nlisten: done\n'),
    ('new at "/z"',
     'listen for\n    new sh.X at "/z"\n        give back created "hi"\n    new: done\nlisten: done\n'),
    ('connection at "/ws"',
     'listen for\n    connection at "/ws"\n        on.open\n            show "open"\n    connection: done\nlisten: done\n'),
]

for label, src in QUOTED_CASES:
    r = run_check(src)
    check_true(f"F2. {label} fails", r.returncode != 0)
    combined = r.stdout + r.stderr
    check_in(f"F2. {label} says unquoted", combined, "unquoted")

print("\n── F2: unquoted still works ──")

c = serve('''\
journey App
    page About at /about
        give back 200 "about page" as text
    page: done
journey: done
''')
r = c.get("/about")
check("unquoted page serves", r.status_code, 200)

print("\n── F2: quoted string as VALUE (not path) — no false positive ──")

r = run_check('''\
journey App
    page Go at /go
        give back 200 "/redirect"
    page: done
journey: done
''')
check("quoted value compiles", r.returncode, 0)

print("\n── F2: mio fmt rewrites quoted path ──")

fmt_src = 'page at "/about"\n    show "hi"\npage: done\n'
r, content = run_fmt(fmt_src, write=True)
if content:
    check_in("fmt rewrites to unquoted", content, 'page at /about')
    check_true("fmt removes quotes", '"/about"' not in content)
    # Rewritten file checks clean
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mho', dir=ROOT,
                                     delete=False, encoding='utf-8') as tmp:
        tmp.write(content); path = tmp.name
    r2 = subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'),
                         'check', path],
                        capture_output=True, text=True, cwd=ROOT)
    check("rewritten file checks clean", r2.returncode, 0)
    try: os.unlink(path)
    except OSError: pass

print("\n── F2: clean file reports canonical ──")

clean_src = 'page at /about\n    show "hi"\npage: done\n'
r, _ = run_fmt(clean_src, write=False)
check_in("canonical file → already canonical", r.stdout, "canonical")

print("\n── F2: edges ──")

# Path with sub-segments
r = run_check('page at "/news/2026"\n    show "hi"\npage: done\n')
check_true("sub-segment path fails", r.returncode != 0)

# Trailing slash
r = run_check('page at "/about/"\n    show "hi"\npage: done\n')
check_true("trailing slash path fails", r.returncode != 0)

# Multiple quoted paths in one file
multi_src = 'page at "/a"\n    show "a"\npage: done\npage at "/b"\n    show "b"\npage: done\n'
r = run_check(multi_src)
check_true("multiple quoted paths fail", r.returncode != 0)


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
