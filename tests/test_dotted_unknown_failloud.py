# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T0-5: an unknown dotted field access fails loud; a known-but-empty one still stays None.

CORRECTED FIX SITE -- verify against current code, do not trust the archived claim. BUILD-LOG
entry 27 named `mohio_interpreter.py:12983` (the `_eval` tail fallback, "Fallback -- wrap as-is")
as the cause of the whole campaign (nums.sum, random.number, random.token, obj.zzz, x as map,
p.nmae). RECONCILIATION-2026-08-07.md's Unit 2a PROVED by instrumentation that :12983 never
fires for any of them -- every one is a `DottedName`, resolved through `Context.get_dotted`
(mohio_interpreter.py:751), which is the real site. This file's fixes are all there.

THE BUG: `MohioValue.get(key)`'s final fallback (mohio_interpreter.py:568, unchanged by this
fix) returns `MohioValue(default)` -- None -- for ANY key it doesn't specifically recognize,
with no way to tell "this key was never there" from "this key exists and is legitimately
empty." get_dotted's "Standard field access" branch (`root.get(p)`) called that fallback
unguarded for every dotted read.

THE FIX (FORK-5, ruled: split, don't blanket fail-loud) -- two narrow, verified-safe additions
to get_dotted's field-access branch, nothing else touched:
  1. A DICT-backed base (a real record: a retrieved row, a shape instance) has a real key set.
     A field NOT in that dict is a typo/unknown field -- fail loud, name the field, the base
     path, and (via difflib) the closest real key. A field that IS a key, even holding None
     (a nullable column, an optional field nobody set), is legitimately empty and stays None.
  2. A LIST-backed base reaching this branch has already skipped every recognized accessor
     (first/last/count/position/pos, handled earlier in get_dotted) -- so any accessor reaching
     here is unrecognized by definition. Fail loud (`.sum`/`.average`/`.total`/`.max`/`.min`
     are not built).
Anything else reaching this branch (a None-valued base -- an unset `as map`/`as int` decl, or a
retrieve/grab that found no row -- or a plain scalar) carries no schema to check a field
against, so it stays exactly as lenient as before: no way to tell a typo from "not populated
yet" without one, and guessing would manufacture false positives on real code.

SEPARATE, NARROWER fix for the security-critical case: `random.token`/`random.hex`/
`random.number` used BARE (no `length N` / `between N and N`) fail to match their own grammar
rule and are silently re-parsed as DottedName(['random', X]) -- a field read on a variable
named `random`, which nothing ever declares. A GENERAL "any undefined parts[0] fails loud"
check was tried first and REVERTED: it broke a real, tested pattern (test_find.py's
`request.cursor` with no real HTTP request in scope -- `request` is legitimately unbound
outside a real request, and a DIFFERENT layer, cursor_pagination_unavailable, is what's meant
to fail loud for that case). This fix is scoped to exactly the `random.{token,hex,number}`
shape instead, both at runtime (get_dotted) and at check time
(mohio_reachability.scan_bare_random_intrinsic) -- `random.uuid`/`random.color` have no
required clause and are unaffected.

Run: `python tests/test_dotted_unknown_failloud.py`.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
from mohio_reachability import run_scans

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
    it = MohioInterpreter()
    try:
        result = it.run(transform(P.parse(src), src))
        return result, None, it
    except MohioRuntimeError as e:
        return None, e, it


CONNECT = 'connect db as sqlite from env.DATABASE_URL\n'
SEED = (CONNECT +
        'save to db.people\n    name "Aria"\n    age 34\nsave: done\n')


print("=== unknown fails loud ===")

_, exc, _ = run_src(
    SEED + 'retrieve p from db.people\n    match name to "Aria"\nretrieve: done\nshow p.nmae\n')
check("p.nmae (typo on a real record) fails loud",
      exc is not None and "not a field on 'p'" in str(exc), exc)
check("names the known fields and suggests the real one",
      exc is not None and 'name' in str(exc) and 'Did you mean' in str(exc), exc)

_, exc, _ = run_src('nums as list 1, 2, 3\nshow nums.sum\n')
check("nums.sum (unsupported list aggregate on a real list) fails loud",
      exc is not None and 'not a supported list operation' in str(exc), exc)

_, exc, _ = run_src('tok random.token\nshow tok\n')
check("random.token (SECURITY CASE) fails loud, not silent None",
      exc is not None and 'random.token needs its required clause' in str(exc), exc)

_, exc, _ = run_src('n random.number\nshow n\n')
check("random.number (bare) fails loud",
      exc is not None and 'random.number needs its required clause' in str(exc), exc)

_, exc, _ = run_src('h random.hex\nshow h\n')
check("random.hex (bare) fails loud",
      exc is not None and 'random.hex needs its required clause' in str(exc), exc)


print("\n=== known-empty legitimately stays None ===")

result, exc, it = run_src('x as map\nshow x\n')
check("x as map (declared, unpopulated) stays None, no error",
      exc is None and [str(s) for s in it.shown] == ['None'], (exc, it.shown))

result, exc, it = run_src('obj as map\nshow obj.zzz\nshow "reached end"\n')
check("obj.zzz on an unpopulated map (no schema to check) stays None, no error",
      exc is None and [str(s) for s in it.shown] == ['None', 'reached end'], (exc, it.shown))

result, exc, it = run_src(
    SEED + 'retrieve p from db.people\n    match name to "Nobody"\nretrieve: done\n'
    'show p.name\nshow "reached end"\n')
check("retrieve that finds nothing, then a dotted read on the unbound name, stays None "
      "(the documented FORK-5 pattern -- the single most load-bearing regression guard here)",
      exc is None and [str(s) for s in it.shown] == ['None', 'reached end'], (exc, it.shown))

_, exc, it = run_src(
    SEED.replace('age 34', 'age 34\n    nickname none') +
    'retrieve p from db.people\n    match name to "Aria"\nretrieve: done\n'
    'show p.nickname\nshow "reached end"\n')
check("a KNOWN field holding a genuine NULL (present key, None value) stays None, no error",
      exc is None and [str(s) for s in it.shown] == ['None', 'reached end'], (exc, it.shown))

_, exc, it = run_src('n random.uuid\nshow "not a length/between form, unaffected"\n')
check("random.uuid (no required clause) is completely unaffected by the random.X fix",
      exc is None, exc)


print("\n=== check-time: mio check catches the random.token/hex/number shape ===")

prog = transform(P.parse('tok random.token\nshow tok\n'), 'tok random.token\nshow tok\n')
errs, warns = run_scans(prog)
_codes = [type(e).__name__ for e in errs]
check("mio check reports an error on bare random.token",
      any('random.token needs its required clause' in str(e) for e in errs), errs)

prog2 = transform(P.parse('n random.uuid\n'), 'n random.uuid\n')
errs2, warns2 = run_scans(prog2)
check("mio check does NOT flag random.uuid (no false positive)",
      not any('random' in str(e) for e in errs2), errs2)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
