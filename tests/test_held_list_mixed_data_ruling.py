# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-QUERY-HELD: find/retrieve/grab over a held list, wired for real -- every case below runs
REAL .mho source through the full pipeline (parse -> transform -> run), never an interpreter
helper called directly. See T1-TEST-REAL-PATH-STANDARD (CLAUDE.md/TESTING.md): this file's
previous version called `interp._filter_held_list(...)` as a bare Python function and reported
"13 passed" while every real .mho program still refused -- the exact trap that standard exists to
catch. Caught 2026-08-09 by asking for the single ground-truth program's real output, not the test
count.

THE WIRING: `_resolve_held_list_source` (mohio_interpreter.py) runs first at each of the three
call sites (_exec_FindBlock, _exec_RetrieveBlock, _exec_GrabBlock). If the source is a held LIST,
`_filter_held_list`'s ruling applies (built and unit-proven previously, now actually reachable).
Every other held-source shape -- a scalar, file content, anything non-list -- still falls through
to `_refuse_held_source_query`, UNCHANGED from T0-6. That refusal is proven here too (cases 6-7),
not just claimed.

Run: `python tests/test_held_list_mixed_data_ruling.py`.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

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


def run_src(src):
    """REAL path: parse -> transform -> it.run(). `show` output lands in it.shown."""
    it = MohioInterpreter()
    return it.run(transform(P.parse(src), src)), it


def run_src_error(src):
    """REAL path, expect a raised MohioRuntimeError. Returns (True, message) or (False, why-not)."""
    try:
        run_src(src)
        return False, "no exception raised"
    except MohioRuntimeError as e:
        return True, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


CONNECT = 'connect db as sqlite from env.DATABASE_URL\n'

WIDGET_GADGET = (
    'create widget\n    name "Widget"\n    price 10\ncreate: done\n'
    'create gadget\n    name "Gadget"\n    price 3\ncreate: done\n'
    'create list products\n    widget\n    gadget\ncreate: done\n')


print("=== 1. find over a held list of records, WHERE on a field -> returns matching records ===")

res, it = run_src(
    CONNECT + WIDGET_GADGET +
    'find matches in products\n    where price is above 5\nfind: done\n'
    'show matches.count\nshow matches.first.name\n')
check("with a db connected, a held-list find over records WHERE-filters instead of refusing",
      it.shown == ['1', 'Widget'] or [str(x) for x in it.shown] == ['1', 'Widget'],
      [str(x) for x in it.shown])


print("\n=== 2. find over a held list of scalars, `where value is ...` -> filters on the value itself ===")
print("    (NOT `it` -- `it` is exclusively the then-chain pipeline pronoun, ruled 2026-08-09)")

res, it = run_src(
    'create list nums\n    3\n    10\n    7\ncreate: done\n'
    'find matches in nums\n    where value is above 5\nfind: done\n'
    'show matches.count\n')
check("a scalar-only list filters on `value` (the scalar itself), not a field",
      [str(x) for x in it.shown] == ['2'], [str(x) for x in it.shown])


print("\n=== 3. match-nothing -> empty result, no crash ===")

res, it = run_src(
    WIDGET_GADGET +
    'find matches in products\n    where price is above 1000\nfind: done\n'
    'show matches.count\n')
check("a WHERE that matches nothing returns an empty result, not an error",
      [str(x) for x in it.shown] == ['0'], [str(x) for x in it.shown])


print("\n=== 4. uniform list, one item missing the field -> exclude + warn, through the REAL path ===")

import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    res, it = run_src(
        'create widget\n    name "Widget"\n    price 10\ncreate: done\n'
        'create gadget\n    name "Gadget"\ncreate: done\n'   # no price field
        'create list products\n    widget\n    gadget\ncreate: done\n'
        'find matches in products\n    where price is above 5\nfind: done\n'
        'show matches.count\n')
printed = buf.getvalue()
check("the item missing 'price' is excluded, only Widget matches",
      [str(x) for x in it.shown] == ['1'], [str(x) for x in it.shown])
check("a warning naming the excluded count and field actually PRINTS through mio run, not just returned",
      "1 item(s)" in printed and "'price'" in printed, printed)


print("\n=== 5. genuinely mixed list (a record + a plain scalar) -> fail loud ===")

ok, detail = run_src_error(
    'create widget\n    name "Widget"\n    price 10\ncreate: done\n'
    'create list mixed\n    widget\n    "just a string"\ncreate: done\n'
    'find matches in mixed\n    where price is above 5\nfind: done\n')
check("a list mixing a record and a scalar raises, naming the shape problem",
      ok and "mixes records and plain values" in detail, detail)


print("\n=== 6. a held SCALAR (not a list) queried with find -> STILL refuses (T0-6 behavior) ===")

ok, detail = run_src_error('hold total 5\nfind x in total\nfind: done\n')
check("a non-list held value still gets the T0-6 refusal, unchanged",
      ok and "held value" in detail and "not built yet in this release" in detail, detail)


print("\n=== 7. querying held FILE CONTENT (miofile.read, a string) -> STILL refuses ===")

import tempfile
_tmpdir = tempfile.mkdtemp()
with open(os.path.join(_tmpdir, "hello.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("hello file content")
os.environ["MIOFILE_ROOT"] = _tmpdir
ok, detail = run_src_error(
    'miofile.read "hello.txt" as content\nfind x in content\nfind: done\n')
check("held file content (a string, not a list) still refuses, unchanged",
      ok and "held value" in detail, detail)
os.environ.pop("MIOFILE_ROOT", None)


print("\n=== 8. REGRESSION: find/retrieve/grab over a REAL db table -> unchanged ===")

DB_SEED = (CONNECT +
           'save to db.items\n    id "1"\n    name "Aria"\nsave: done\n'
           'save to db.items\n    id "2"\n    name "Bo"\nsave: done\n')

res, it = run_src(DB_SEED + 'find rows in db.items\nfind: done\nshow rows.count\n')
check("find over a real db table is unaffected", [str(x) for x in it.shown] == ['2'], it.shown)

res, it = run_src(DB_SEED +
                   'retrieve one from db.items\n    match id to "2"\nretrieve: done\n'
                   'show one.name\n')
check("retrieve over a real db table is unaffected", [str(x) for x in it.shown] == ['Bo'], it.shown)

res, it = run_src(DB_SEED +
                   'grab g from db.items\n    match id to "1"\ngrab: done\nshow g.name\n')
check("grab over a real db table is unaffected", [str(x) for x in it.shown] == ['Aria'], it.shown)


print("\n=== 9. REGRESSION: random from / repeat each over a held list -> unchanged ===")

seen = set()
for _ in range(20):
    res, it = run_src(
        'colors as list "red", "blue", "green"\npick random from colors\nshow pick\n')
    seen.add(str(it.shown[0]) if it.shown else None)
check("random from a held list still works and varies",
      seen <= {'red', 'blue', 'green'} and len(seen) > 1, seen)

res, it = run_src(
    'colors as list "red", "blue", "green"\nrepeat each c in colors\n    show c\nrepeat: done\n')
check("repeat each over a held list still works, in order",
      [str(x) for x in it.shown] == ['red', 'blue', 'green'], it.shown)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
