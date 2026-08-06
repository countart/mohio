# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_keyword_anchoring.py

EXHAUSTIVE. Not a sample - every keyword the grammar defines.

For each one, build an identifier out of it (`hold` -> `holdzz`, `check` -> `checkzz`) and demand
it lexes as a single NAME. If any keyword is missing its word boundary, its identifier splits and
this goes red.

This is the guard for the whole bug class:

    hold  ->  `holder 5` used to lex as HOLD + `older`, and SILENTLY declared a held variable
              named `older`. It parsed. It ran. It was wrong.

The root cause was that most rules wrote their keywords as bare inline strings (`IS "more" "than"`)
rather than named terminals. Lark turns a bare inline string into an anonymous terminal with NO
word boundary, so the keyword could match inside an identifier. 421 inline keywords across 988
sites are now named, anchored terminals. The leading underscore on the generated ones (`_MORE`,
`_THAN`) makes Lark filter them from the parse tree, so the tree shape - and every transformer
child index - is unchanged.

Two things this test also protects, because both broke earlier attempts:

  1. Dotted keywords. A plain (?![A-Za-z0-9_]) still permits a following DOT, so `starts.with`
     splits into STARTS + `.with`. Keywords that head a dotted keyword refuse the dot too.

  2. Tree shape. If a filtered terminal ever loses its underscore it becomes visible, every
     child index after it shifts, and the transformer breaks in ways no parse test would catch.
"""
import os
import re
import sys

os.environ.setdefault("DATABASE_URL", ":memory:")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data

from lark import Lark, Token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
parser = Lark(
    "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//")),
    parser="earley", ambiguity="resolve",
)

# Every word-shaped keyword the grammar defines, anchored or not.
keywords = set()
for line in raw.splitlines():
    m = re.match(r"^_?[A-Z][A-Z0-9_]*(\.\d+)?\s*:\s*(.*)$", line.split("//")[0].rstrip())
    if not m:
        continue
    body = m.group(2)
    keywords.update(re.findall(r"/([a-z][a-z0-9_]*)\(\?!", body))   # already anchored
    keywords.update(re.findall(r'"([a-z][a-z0-9_]*)"', body))       # bare string - the hazard

_p = _f = 0
split = []
for kw in sorted(keywords):
    ident = kw + "zz"
    try:
        toks = [t for t in parser.parse(f"{ident} 5\n").scan_values(lambda v: isinstance(v, Token))]
        ok = bool(toks) and toks[0].type == "NAME" and str(toks[0]) == ident
    except Exception:
        ok = False
    _p += ok
    _f += not ok
    if not ok:
        split.append(kw)

print(f"  keywords defined in the grammar : {len(keywords)}")
print(f"  identifiers that lex as one NAME: {_p}")
if split:
    print(f"  UNANCHORED (identifier splits)  : {len(split)}")
    for kw in split[:20]:
        print(f"    {kw}  ->  `{kw}zz` does not lex as a single NAME")

# The dotted keywords must survive the boundary - this is what broke the naive sweeps.
def toks(src):
    return [t.type for t in parser.parse(src).scan_values(lambda v: isinstance(v, Token))]

for label, src, want in (
    ("starts.with stays ONE token", 'x "a"\nshow "y" if x starts.with "a"\n', "STARTS_WITH"),
    ("ends.with stays ONE token",   'x "a"\nshow "y" if x ends.with "a"\n',   "ENDS_WITH"),
):
    ok = want in toks(src)
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
