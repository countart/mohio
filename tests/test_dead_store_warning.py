# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Assigned-but-never-read (dead store) earns a check-time WARNING, never an error.

`print "hello world"` checks clean and runs silently: with no `print` keyword the line is absorbed
as a bare `NAME value` assignment (print = "hello world") and nothing is shown. This is the exact
trap that stranded two pioneers. The fix is a GENERAL detector -- any top-level `name value` whose
name is never read anywhere warns -- NOT a foreign-keyword blocklist (the known-keyword list only
enriches the hint text, it never decides whether the warning fires).

Adversarial angles locked here:
  1. the pioneer case (`print "..."`) warns, with a "did you mean show" hint.
  2. a wrong-case verb (`Show "..."`) fails loud as a hard error (A4) -- not a variable.
  3. it is a WARNING, not an error (ctx.errors stays empty -- the gate must stay green).
  4. a legit assign-then-read does NOT warn (no false positive).
  5. a value read only DEEP INSIDE a block still counts as used (reads are global) -- no warning.
  6. an unused LOCAL inside a block does NOT warn (top-level scope only -- that is ordinary noise).
  7. reassignment then read does NOT warn.
  8. the hint list changes only the hint TEXT; an unknown leading word still warns (generic hint).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_symbol_table import extract_symbols
from mohio_transformer import MOHIO_RESERVED_EXACT, validate
from mohio_pretokenizer import pretokenize

_raw = Path('mohio.lark').read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def analyze(src):
    st = extract_symbols(src)
    ps = pretokenize(src, st.all_user_names(), MOHIO_RESERVED_EXACT)
    ctx = validate(P.parse(ps), source=src, filename="t.mho", symbol_table=st)
    ds = [str(w) for w in ctx.warnings if 'is set but never used' in str(w)]
    return ctx, ds


print("=== 1. pioneer case: print \"...\" warns with a show hint ===")
ctx, ds = analyze('print "hello world"\n')
check("print warns (dead store)", len(ds) == 1, str(ds))
check("hint points at show", ds and 'show' in ds[0].lower(), str(ds))

print("\n=== 2. wrong-case verb Show \"...\" is now a hard ERROR (A4), not a warning ===")
# A4 escalated a case-variant of a real verb from the dead-store warning to a hard error:
# `Show` is not a variable, it is a mis-cased keyword. It is no longer a dead-store warning.
ctx, ds = analyze('Show "Hello, world"\n')
errs = [str(e) for e in ctx.errors]
check("Show is a hard error, not the dead-store warning", len(ds) == 0 and len(errs) == 1, str(errs))
check("error says it is not a Mohio word", errs and 'not a mohio word' in errs[0].lower(), str(errs))
check("error says keywords are lowercase", errs and 'lowercase' in errs[0].lower(), str(errs))

print("\n=== 2b. A4 is NARROW: an UNKNOWN capitalized word stays a WARNING, never a hard error ===")
# GAP-2 regression (mutation S3c, 2026-07-31). A4 escalates a case-variant of a REAL verb (`Show`)
# to a hard error, but an unknown capitalized word (`Frobnicate`) is NOT a mis-cased keyword -- it
# is just an unread top-level assignment, and must stay a dead-store WARNING. Mutating the guard
# from `if low in _DEAD_STORE_VERBS and name != low` to `if name != low` (fire on ANY capitalized
# leading word) passed the whole suite before this case: the positive half of A4 was tested, the
# NARROW boundary was not. This locks the negative half -- the ratified "narrow escalation only".
ctx, ds = analyze('Frobnicate 5\n')
errs = [str(e) for e in ctx.errors]
check("an unknown capitalized word is a dead-store WARNING (not escalated)", len(ds) == 1, str(ds))
check("an unknown capitalized word raises NO A4 error (narrow escalation only)",
      len(errs) == 0, str(errs))

print("\n=== 3. dead store is a WARNING, not an error (gate stays green) ===")
ctx, ds = analyze('print "hello world"\n')
check("no error raised for a dead store", len(ctx.errors) == 0,
      str([str(e) for e in ctx.errors]))

print("\n=== 4. legit assign-then-read does NOT warn ===")
_, ds = analyze('score 10\nshow score\n')
check("no false positive when the value is read", len(ds) == 0, str(ds))

print("\n=== 5. read DEEP INSIDE a block counts as used (reads are global) ===")
src = ('threshold 5\n'
       'check threshold\n    when threshold is more than 3\n        show "high"\ncheck: done\n')
_, ds = analyze(src)
check("top-level value read inside a check body -> no warning", len(ds) == 0, str(ds))

print("\n=== 6. unused LOCAL inside a block does NOT warn (top-level scope only) ===")
src = ('task greet name as text\n    unused 99\n    give back ("Hi " & name)\ntask: done\n'
       'call greet with "Bo"\n')
_, ds = analyze(src)
check("unused local inside a task -> no warning (not the newcomer trap)", len(ds) == 0, str(ds))

print("\n=== 7. reassignment then read does NOT warn ===")
_, ds = analyze('score 10\nscore 20\nshow score\n')
check("assigned twice then read -> no warning", len(ds) == 0, str(ds))

print("\n=== 7b. a DOTTED read counts as used (USERVAR_DOTTED path) ===")
# regression: the reads walk must handle dotted user vars (user.name) without crashing and must
# count the base name (user) as read. A single-name-only test missed this and let a NameError ship.
src = ('users as list "a", "b"\n'
       'repeat each user in users\n    show user.name\nrepeat: done\n')
ctx, ds = analyze(src)
check("no crash + `users` read via `user.name` chain -> no warning", len(ds) == 0, str(ds))
check("validate produced no errors on the dotted program", len(ctx.errors) == 0,
      str([str(e) for e in ctx.errors]))

print("\n=== 7c. a real multi-construct program checks clean (crash guard) ===")
# the CLAUDE.md canonical quick-reference block crashed the reads walk (dotted vars). Lock a
# compact stand-in: dotted reads across several block types must validate without raising.
src = ('connect db as postgres from env.DATABASE_URL\n'
       'retrieve player from db.players\n    match id to 1\nretrieve: done\n'
       'show player.name\n')
try:
    ctx, ds = analyze(src)
    ok = True
except Exception as e:
    ok = False; ds = [f"<crash: {type(e).__name__}: {e}>"]
check("multi-construct program with dotted reads does not crash validate", ok, str(ds))

print("\n=== 8. hint list changes TEXT only -- an unknown word still warns ===")
_, ds = analyze('frobnicate 5\n')
check("unknown leading word still warns (proves it is not a blocklist)", len(ds) == 1, str(ds))
check("generic case reads as unused-declaration, not a did-you-mean",
      ds and 'Declared but never read' in ds[0] and 'did you mean' not in ds[0].lower(), str(ds))

print("\n=== 9. a top-level var read ONLY via a dotted access counts as used (M7 gap regression) ===")
# `person` is read solely as `person.name` (a USERVAR_DOTTED access), never as a bare name. If the
# reads walk ever stops collecting dotted reads, `person` would warn falsely as "never used".
# Mutation testing (2026-07-31) found the suite did not cover this -- the earlier "dotted read"
# case read its variable via a PLAIN name (`repeat each user in users`). Behavior is correct
# today; this locks it in.
_, ds = analyze('person "Bo"\nshow person.name\n')
check("a top-level var read only via `person.name` is NOT a dead store", len(ds) == 0, str(ds))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
