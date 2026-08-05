# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Regression guard for scan_typos: statement-leading assignments one edit from
an action verb should warn ('did you mean show?'), while clean code and real
variable names must not. Locks both the edit-distance helper and the AST pass."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from lark import Lark
from mohio_transformer_ast import transform
from mohio_reachability import scan_typos, _edit_distance_one

_raw = open(os.path.join(_ROOT, "mohio.lark"), encoding="utf-8").read()
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


def warns(src):
    return scan_typos(transform(P.parse(src), src))


# edit-distance helper: each typo class is exactly one edit away
check("ed: insertion (shoow~show)", _edit_distance_one("shoow", "show"))
check("ed: deletion (shw~show)", _edit_distance_one("shw", "show"))
check("ed: substitution (cache~cathe)", _edit_distance_one("cathe", "cache"))
check("ed: transposition (svae~save)", _edit_distance_one("svae", "save"))
check("ed: transposition (chcek~check)", _edit_distance_one("chcek", "check"))
check("ed: identical is not one edit", not _edit_distance_one("show", "show"))
check("ed: distance-2 is not one edit", not _edit_distance_one("report", "render"))

# scan_typos: typos warn, clean code does not
check("typo shoow warns", len(warns(H + 'shoow "hi"\n')) == 1)
check("typo chcek warns", len(warns(H + 'chcek "hi"\n')) == 1)
check("typo svae warns", len(warns(H + 'svae "hi"\n')) == 1)
check("real variable (current_room) does not warn",
      len(warns(H + 'current_room "west"\n')) == 0)
check("short name (max) does not warn (< 4 chars)",
      len(warns(H + 'max "hi"\n')) == 0)
check("exact verb as var name not flagged",
      len(warns(H + 'render "hi"\n')) == 0)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
