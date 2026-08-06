# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Regression guard for the `render ... render: done` view block (HTML Slice 1).
render is the canonical view container: it captures literal HTML and interpolates
{{ }} the same way `show ... show: done` does, while `show` itself is unchanged.
Later slices layer auto-escaping and mio.* helpers onto this foundation."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import mohio_data

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import ShowBlock

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)
H = "connect db as sqlite from env.DATABASE_URL\n"

PASS = 0
FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def run_out(src):
    t = transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run(t, {})
    return t, "".join(str(x) for x in it.shown)


t, out = run_out(H + 'hold name = "World"\nrender\n    <h1>Hi {{ name }}</h1>\nrender: done\n')
check("render block produces a view node", any(isinstance(s, ShowBlock) for s in t.statements))
check("render interpolates {{ }}", "Hi World" in out)
check("render emits literal HTML", "<h1>" in out)

_, static = run_out(H + 'render\n    <div>static</div>\nrender: done\n')
check("render with static HTML", "static" in static)

# `show` block must still work — render is additive, not a replacement
_, shown = run_out(H + 'hold n = "X"\nshow\n    <p>{{ n }}</p>\nshow: done\n')
check("show block still works unchanged", "X" in shown and "<p>" in shown)

# render AUTO-ESCAPES interpolated values (XSS protection), literal markup stays raw
_, esc = run_out(H + 'hold name = "<script>alert(1)</script>"\nrender\n    <h1>Hi {{ name }}</h1>\nrender: done\n')
check("render escapes interpolated value", "&lt;script&gt;" in esc)
check("render leaves literal markup raw", "<h1>" in esc)
check("render lets no raw <script> through", "<script>" not in esc)

# show block does NOT escape (value/API emit context, backward compat)
_, rawout = run_out(H + 'hold v = "<b>x</b>"\nshow\n    <p>{{ v }}</p>\nshow: done\n')
check("show block stays raw (not escaped)", "<b>x</b>" in rawout)

# render wraps the body in a full HTML5 document with title/describe in the head
_, page = run_out(H + 'title "Rates"\ndescribe "Cabin pricing"\nrender\n    <h1>Hello</h1>\nrender: done\n')
check("render wraps body in full document", "<!DOCTYPE html>" in page and "<body>" in page)
check("title populates the head", "<title>Rates</title>" in page)
check("describe populates the head", 'content="Cabin pricing"' in page)
check("render body content present", "<h1>Hello</h1>" in page)

# render that is already a full document is NOT double-wrapped
_, full = run_out(H + 'render\n    <!DOCTYPE html><html><body>full</body></html>\nrender: done\n')
check("no double-wrap when author wrote a full document", full.count("<!DOCTYPE") == 1)

# show block is never shell-wrapped
_, frag = run_out(H + 'show\n    <p>frag</p>\nshow: done\n')
check("show block is not shell-wrapped", "<!DOCTYPE" not in frag and "<p>frag</p>" in frag)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
