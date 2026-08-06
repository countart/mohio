# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""grab / get / remove support a COMPOSITE match target (2026-08-04).

grab/get took a single match field and silently dropped a composite match (`match a to X, b to
Y`) to `node.match = None` entirely -- the transformer's `_single_fetch_block` only ever looked
for a lone `MatchClause`, but `match_clause()` returns a LIST of `MatchClause` for multiple
comma-separated pairs. grab's "no match = bind nothing, not a failure" design meant a composite
match silently returned nothing with NO error -- worse than "first field only", the exact
"executes, does the wrong thing, no error" class this project treats as highest severity.

remove had the identical root cause but a safer symptom: `_exec_RemoveBlock` fails loud when
`condition` is falsy, so a composite-match remove refused with a confusing "remove needs a
condition" message even though one was given.

Fixed across four layers, mirroring the earlier upsert composite-match fix:
  - transformer (_single_fetch_block, remove_block): captures the full list, not just the first
  - retrieve_one_multi: already existed on all four backend classes but was NEVER WIRED to
    anything (dead code) -- now actually called for a composite grab/get
  - remove_multi: added to all four backend classes, mirroring update_multi
  - interpreter (_exec_GrabBlock, _exec_RemoveBlock): normalize match/condition, single-field
    path unchanged (proven), composite path uses the multi-field DB methods

Run: `python tests/test_grab_get_remove_composite_match.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def run(src):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    it.shown = []
    it.run(prog)
    return it.shown

#   NOTE: the composite key deliberately does NOT use the field name `id` -- Mohio's
#   auto-schema gives `id` a real, table-wide PRIMARY KEY (the Stage 1 architectural finding:
#   no other column ever gets a uniqueness guarantee), so reusing an `id` value across two
#   `grp`s (the exact real-world session-scoped-item scenario this fix targets) would violate
#   that constraint and mask the composite-match behavior under an unrelated write error.
#   `item_id` here is an ordinary TEXT column with no uniqueness of its own -- only the PAIR
#   (grp, item_id) is meant to be unique, which is precisely the case a composite match exists
#   to handle.
SETUP = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'save to db.t\n    grp "s1"\n    item_id "A"\n    name "apple"\nsave: done\n'
    'save to db.t\n    grp "s1"\n    item_id "B"\n    name "banana"\nsave: done\n'
    'save to db.t\n    grp "s2"\n    item_id "A"\n    name "OTHER-apple"\nsave: done\n'
)

# ── grab: composite match must select the RIGHT row (session-scoped item_id collision case) ─
GRAB_COMPOSITE = SETUP + ('grab rec from db.t\n    match grp to "s2", item_id to "A"\n'
                          'grab: done\nshow "grab -> {{ rec.name }}"\n')
check("composite grab selects the correct row (grp=s2 AND item_id=A -> OTHER-apple, not apple)",
      run(GRAB_COMPOSITE) == ['grab -> OTHER-apple'], str(run(GRAB_COMPOSITE)))

# ── get: same executor as grab, same proof ──────────────────────────────────────────────────
GET_COMPOSITE = SETUP + ('get rec from db.t\n    match grp to "s1", item_id to "B"\n'
                         'get: done\nshow "get -> {{ rec.name }}"\n')
check("composite get selects the correct row (grp=s1 AND item_id=B -> banana)",
      run(GET_COMPOSITE) == ['get -> banana'], str(run(GET_COMPOSITE)))

# ── single-field grab/get unaffected (the proven, already-working path) ────────────────────
GRAB_SINGLE = SETUP + 'grab rec from db.t\n    match name to "apple"\ngrab: done\nshow "single -> {{ rec.name }}"\n'
check("single-field grab unaffected", run(GRAB_SINGLE) == ['single -> apple'], str(run(GRAB_SINGLE)))

# ── grab with NO match at all still binds nothing, no error (unchanged design) ─────────────
GRAB_NONE = SETUP + 'grab rec from db.t\ngrab: done\nshow "empty -> {{ rec }}"\nshow "done"\n'
r = run(GRAB_NONE)
check("grab with no match clause still just binds nothing (no crash)", r[-1] == 'done', str(r))

# ── remove: composite match deletes ONLY the matching row, leaves siblings untouched ────────
REMOVE_COMPOSITE = SETUP + (
    'remove from db.t\n    match grp to "s1", item_id to "A"\nremove: done\n'
    'find left in db.t\nfind: done\nshow "count {{ left.count }}"\n'
)
r = run(REMOVE_COMPOSITE)
check("composite remove deletes exactly the matched row (3 -> 2, not all-or-nothing)",
      r == ['count 2'], str(r))

REMOVE_COMPOSITE_VERIFY = SETUP + (
    'remove from db.t\n    match grp to "s1", item_id to "A"\nremove: done\n'
    'grab still_here from db.t\n    match grp to "s2", item_id to "A"\ngrab: done\n'
    'show "sibling survives -> {{ still_here.name }}"\n'
)
r = run(REMOVE_COMPOSITE_VERIFY)
check("composite remove does not touch the sibling row sharing one field",
      r == ['sibling survives -> OTHER-apple'], str(r))

# ── single-field remove unaffected ──────────────────────────────────────────────────────────
REMOVE_SINGLE = SETUP + ('remove from db.t\n    match name to "banana"\nremove: done\n'
                         'find left in db.t\nfind: done\nshow "count {{ left.count }}"\n')
check("single-field remove unaffected", run(REMOVE_SINGLE) == ['count 2'], str(run(REMOVE_SINGLE)))

# NOTE: `_exec_RemoveBlock`'s `if not cond:` safety net (raises `remove_without_condition`) is
# untouched by this fix -- it sits directly above the new composite-match branch, unchanged.
# It is not exercised here: the grammar requires `remove_condition` (no `?`), so `remove from
# db.t` with genuinely nothing after it does not even parse -- there is no real Mohio source
# that reaches `cond` being falsy. Testing it would mean fabricating interpreter-internal state
# no real program can produce, which tests nothing real. Confirmed unchanged by inspection.
def run_raises(src):
    # it.run() catches _Raise internally and returns it as {'status': 500, 'body': message} --
    # it never propagates as a Python exception to a plain (non-served) caller. Check the
    # returned dict, not a raised exception.
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    result = it.run(prog)
    if isinstance(result, dict) and result.get('status') == 500:
        return str(result.get('body'))
    return None

# ── remove: a non-equality where inside a SINGLE condition still fails loud (unchanged) ────
AMOUNT_SETUP = 'connect db as sqlite from env.DATABASE_URL\nsave to db.t\n    amount 5\nsave: done\n'
err = run_raises(AMOUNT_SETUP + 'remove from db.t\n    where amount is more than 1\nremove: done\n')
check("remove with a comparison-operator where still fails loud (single-condition path unaffected)",
      err is not None and 'equality' in err.lower(), str(err))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
