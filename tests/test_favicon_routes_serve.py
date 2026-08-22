# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Favicon 404 bug (found by platform chat, confirmed live 2026-08-15, affects every tenant
app). `favicon.ico` / `favicon-32x32.png` / `favicon-16x16.png` / `apple-touch-icon.png` were
wired as dedicated `Route()` entries pointed at `serve_static`, which read the requested
filename from `request.path_params.get("path", "")` -- a param only the `{path:path}`
catch-all route ever populates. The four dedicated routes matched a fixed literal path with no
`{path:path}` placeholder, so `path_params["path"]` was always `""` for them,
`_static_file_response("")` always returned `None` (its own first line: `if not path: return
None`), and they intercepted ahead of the catch-all -- every tenant's favicon 404'd
unconditionally, real file or not.

Fixed: removed the four dedicated routes entirely. These filenames now fall through to the
existing `{path:path}` catch-all (`serve_page_or_static`), which already resolves an arbitrary
static filename correctly -- the dedicated routes added nothing but the bug. `serve_static`
itself (now unreferenced) was removed alongside them rather than left as dead, broken code.

Real HTTP throughout (Starlette TestClient over MohioServer/create_app), a real static
directory on disk, real file bytes compared on the wire -- not a unit call.

Run: `python tests/test_favicon_routes_serve.py`.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

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

SRC = 'page Home at /home\n    render\n        <p>[HOME]</p>\n    render: done\npage: done\n'
FAVICON_FILES = ["favicon.ico", "favicon-32x32.png", "favicon-16x16.png", "apple-touch-icon.png"]


def make_client(app_dir):
    prog = transform(P.parse(SRC), SRC)
    interp = MohioInterpreter()
    server = MohioServer(prog, interp, app_dir=app_dir)
    return TestClient(create_app(server), raise_server_exceptions=False)


# ── Case 1: all four favicon files present -- must 200 with the real bytes ──────────────
present_dir = tempfile.mkdtemp()
contents = {}
for name in FAVICON_FILES:
    data = f"REAL-BYTES-FOR-{name}".encode()
    contents[name] = data
    with open(os.path.join(present_dir, name), 'wb') as fh:
        fh.write(data)
other_bytes = b"ARBITRARY-PNG-BYTES"
with open(os.path.join(present_dir, "anything.png"), 'wb') as fh:
    fh.write(other_bytes)

c_present = make_client(present_dir)

for name in FAVICON_FILES:
    r = c_present.get(f"/{name}")
    check(f"GET /{name} (file present) -> 200 with the real file bytes",
          r.status_code == 200 and r.content == contents[name],
          f"status={r.status_code} content={r.content[:60]!r}")

# Regression: a normal, arbitrarily-named static file still serves via the catch-all,
# unaffected by removing the four dedicated routes.
r_other = c_present.get("/anything.png")
check("regression: an arbitrary static file still serves via the {path:path} catch-all",
      r_other.status_code == 200 and r_other.content == other_bytes,
      f"status={r_other.status_code} content={r_other.content[:60]!r}")


# ── Case 2: favicon files genuinely absent -- must be a real 404, not the bug's blanket one ──
absent_dir = tempfile.mkdtemp()
with open(os.path.join(absent_dir, "unrelated.txt"), 'wb') as fh:
    fh.write(b"not a favicon")

c_absent = make_client(absent_dir)

for name in FAVICON_FILES:
    r = c_absent.get(f"/{name}")
    check(f"GET /{name} (file absent) -> a real 404 (not a favicon at all in this app)",
          r.status_code == 404, f"status={r.status_code}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
