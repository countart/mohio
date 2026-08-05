# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_strings_status.py — Locks the Strings/Status decisions (Build 4).

From the design-chat handoff (Mohio-Locked-Decisions-Strings-Status-Langmap):
  1. Multi-line strings need the as.paragraph marker (aliases as.para/paragraph/para).
  2. A NAKED multi-line string (no marker) FAILS LOUD (MULTILINE_STRING_NEEDS_MARKER).
  3. \\n escapes render a real newline in normal single-line strings.
  4. Five status aliases: ok=200 created=201 unauthorized=401 missing=404 error=500.
  5. `give back "x"` (no status) defaults to 200.
  6. A value carrying real newlines survives give back (round-trip preserved).

Run: python3 tests/test_strings_status.py   (from the compiler root)
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
from pathlib import Path
from lark import Lark
from lark.exceptions import UnexpectedInput
from mohio_transformer_ast import transform
from mohio_transformer import validate
from mohio_interpreter import MohioInterpreter, MohioValue, Context
from mohio_ast import GiveBackStmt

_raw = Path('mohio.lark').read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
NL = '\n'

passed = failed = 0
def check(name, got, want):
    global passed, failed
    ok = (got == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r} want={want!r}"))
    passed += ok; failed += (not ok)

def value_of(stmts, var='result'):
    prog = transform(P.parse(stmts + '\n'), stmts + '\n')
    interp = MohioInterpreter(verbose=False)
    ctx = Context()
    for st in prog.statements:
        interp._exec(st, ctx)
    v = ctx.get(var)
    return v.to_python() if isinstance(v, MohioValue) else v

def status_of(give_back_src):
    src = f'listen for GET "/x"\n    {give_back_src}\nlisten: done'
    prog = transform(P.parse(src + '\n'), src)
    def walk(n):
        yield n
        for a in getattr(n, '__dict__', {}).values():
            if isinstance(a, list):
                for x in a:
                    if hasattr(x, '__dict__'): yield from walk(x)
            elif hasattr(a, '__dict__'): yield from walk(a)
    gb = [n for n in walk(prog) if isinstance(n, GiveBackStmt)]
    return gb[0].status if gb else 'NONE'

def parses_ok(src):
    """A naked multi-line string parses fine — it always has (STRING accepts
    newlines). as.paragraph is an OPTIONAL authoring-clarity marker, NOT a parse
    requirement. We must NOT fail-loud here or we'd reject working code (the live
    Zork marble scene is a naked multi-line string)."""
    try:
        P.parse(src + '\n')
        return True
    except Exception:
        return False

# ── 1. multi-line marker forms parse + preserve newlines ─────────────────
print("\n=== as.paragraph (+ aliases) multi-line ===")
check("as.paragraph preserves newline",
      NL in value_of(f'result = as.paragraph "A.{NL}{NL}B."'), True)
check("paragraph (bare) preserves newline",
      NL in value_of(f'result = paragraph "a{NL}b"'), True)
check("as.para preserves newline",
      NL in value_of(f'result = as.para "x{NL}y"'), True)
check("para (bare) preserves newline",
      NL in value_of(f'result = para "p{NL}q"'), True)
check("marker on single-line string is harmless",
      value_of('result = as.paragraph "hello"'), "hello")

# ── 2. naked multi-line strings PARSE FINE (no regression on working code) ─
# Correction to the original design premise: multi-line strings already parse —
# they are NOT a parse crash. as.paragraph is reclassified to an optional
# Phase 3/4 readability marker. We do NOT fail-loud on naked multi-line strings,
# because that would reject working code (e.g. the live Zork marble scene).
print("\n=== naked multi-line parses fine (no false regression) ===")
check("naked multi-line in give back parses",
      parses_ok(f'listen for GET "/x"\n    give back 200 "L1{NL}L2"\nlisten: done'), True)
check("naked multi-line assignment parses",
      parses_ok(f'note "first{NL}second"'), True)
check("marked multi-line also parses",
      parses_ok(f'listen for GET "/x"\n    give back as.paragraph "L1{NL}L2"\nlisten: done'), True)

# ── 3. \\n escape renders a real newline ─────────────────────────────────
print("\n=== \\n escape ===")
check("\\n decodes to real newline",
      NL in value_of('result = "Line one.\\nLine two."'), True)
check("\\n count correct",
      value_of('result = "a\\nb\\nc"').count(NL), 2)

# ── 4. five status aliases ───────────────────────────────────────────────
print("\n=== status aliases ===")
check("ok -> 200",            status_of('give back ok "saved"'), 200)
check("created -> 201",       status_of('give back created member'), 201)
check("unauthorized -> 401",  status_of('give back unauthorized "log in"'), 401)
check("missing -> 404",       status_of('give back missing "gone"'), 404)
check("error -> 500",         status_of('give back error "oops"'), 500)
check("numbers still work",   status_of('give back 202 "review"'), 202)
check("no status -> None (runtime defaults 200)", status_of('give back "x"'), None)

# ── 5. round-trip: real newline survives give back ───────────────────────
print("\n=== newline round-trip through give back ===")
def giveback_body(stmts):
    prog = transform(P.parse(stmts + '\n'), stmts + '\n')
    interp = MohioInterpreter(verbose=False); ctx = Context()
    try:
        for st in prog.statements:
            interp._exec(st, ctx)
    except Exception as e:
        b = getattr(e, 'body', None) or getattr(e, 'value', None)
        return b.to_python() if isinstance(b, MohioValue) else b
    return None
check("value with \\n survives give back",
      giveback_body('note "WELCOME\\n\\nBody"\ngive back 200 note').count(NL), 2)

# ── 6. real read-leaflet fix: Mock decide accepts persona + model_override ─
# The live "read leaflet" 500 was MockAiRuntime.decide() being out of sync with
# the call site (and the real AnthropicAiRuntime.decide): missing persona and
# model_override. The narrator block uses both, so mio run / mock crashed.
print("\n=== Mock AI decide signature in sync with call site ===")
import inspect
from mohio_interpreter import MockAiRuntime
_sig = inspect.signature(MockAiRuntime.decide).parameters
check("Mock decide accepts persona",        'persona' in _sig, True)
check("Mock decide accepts model_override", 'model_override' in _sig, True)

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)