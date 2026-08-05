# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Standalone typed declaration (`x as int`) + scalar type enforcement.

The empty typed declaration that closes the `x as int 5` drift channel, plus the enforcement that
makes a declared type MEAN something: a contract on the name, checked on every assignment.

Design (locked with Ronnie):
- `x as int` with no value DECLARES a contract and seeds the type-zero (0 / 0.0 / "" / false).
- Reading before assignment returns that empty value (NOT fail-loud) -- Ronnie's call.
- Every later assignment must satisfy the contract or FAIL LOUD. "5" (text) does not satisfy int
  -- the whole point is that text-that-looks-numeric is still text.
- A BARE variable (never declared) stays fully malleable -- no contract, no enforcement.
- The contract lives on the NAME and persists; to change it you `release` (drop contract, keep
  value) or `forget` (remove the name). A type is never silently redefined.

This is the standalone equivalent of a shape field `age as int`, built on the same enforcement.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context

_raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def run(src):
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


def fails(src):
    try:
        run(src)
        return False
    except Exception:
        return True


# ── declaration + read-before-assign (type-zero) ──────────────────────────────────────
check("`x as int` declares, reads back 0", run('x as int\ngive back 200 x') == 0)
check("`x as dec` reads back 0.0", run('x as dec\ngive back 200 x') == 0.0)
check("`x as text` reads back empty string", run('x as text\ngive back 200 x') == "")
check("`x as boolean` reads back false", run('x as boolean\ngive back 200 x') is False)

# ── enforcement: valid assignments satisfy the contract ───────────────────────────────
check("`x as int` then `x 5` -> 5", run('x as int\nx 5\ngive back 200 x') == 5)
check("`x as int` then x 5 then x 10 -> 10 (reassign under contract)",
      run('x as int\nx 5\nx 10\ngive back 200 x') == 10)
check("`x as text` then `x \"hi\"` -> hi", run('x as text\nx "hi"\ngive back 200 x') == "hi")
check("`x as dec` then `x 5.5` -> 5.5", run('x as dec\nx 5.5\ngive back 200 x') == 5.5)
check("`x as dec` then `x 5` (int satisfies dec) -> 5", run('x as dec\nx 5\ngive back 200 x') == 5)

# ── enforcement: violations fail loud ─────────────────────────────────────────────────
check("`x as int` then `x \"cat\"` FAILS", fails('x as int\nx "cat"\ngive back 200 x'))
check("`x as int` then `x \"5\"` FAILS (text is not int, even if numeric-looking)",
      fails('x as int\nx "5"\ngive back 200 x'))
check("`x as text` then `x 5` FAILS (int is not text)", fails('x as text\nx 5\ngive back 200 x'))
check("`x as int` then `x 5.5` FAILS (decimal is not int)",
      fails('x as int\nx 5.5\ngive back 200 x'))

# ── bare variables stay malleable (no contract, no enforcement) ───────────────────────
check("bare `y 5` then `y \"cat\"` is allowed (no contract)",
      run('y 5\ny "cat"\ngive back 200 y') == "cat")
check("bare `y 5` then `y 10` -> 10", run('y 5\ny 10\ngive back 200 y') == 10)
check("bare `y \"a\"` then `y \"b\"` -> b", run('y "a"\ny "b"\ngive back 200 y') == "b")

# ── the retired form still fails loud (unchanged) ─────────────────────────────────────
check("`x as int 5` (type-before-value) still fails loud", fails('x as int 5\ngive back 200 x'))

# ── the type checker unit-level (the enforcement core) ────────────────────────────────
it = MohioInterpreter()
check("checker: 5 matches int", it._value_matches_type(5, 'int') is True)
check("checker: '5' does NOT match int", it._value_matches_type('5', 'int') is False)
check("checker: True does NOT match int (bool is not int)",
      it._value_matches_type(True, 'int') is False)
check("checker: None matches any type (empty is valid)", it._value_matches_type(None, 'int') is True)
check("checker: dec.2 resolves to dec base", it._value_matches_type(5.5, 'dec.2') is True)

# ── context contract tracking ─────────────────────────────────────────────────────────
ctx = Context()
ctx.declare_type('a', 'int')
check("context: typed_of after declare", ctx.typed_of('a') == 'int')
check("context: untype_name clears the contract",
      ctx.untype_name('a') is True and ctx.typed_of('a') is None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
