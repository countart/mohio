# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Design literals (v3.8) must carry their value, not silently become None.

The grammar defines color_lit (#ff8800), percent_lit (50%), and dimension_lit (12px), and
the interpreter has evaluators that wrap each as a typed MohioValue ('color'/'percent'/
'dimension'). But no transformer method built the nodes, and the node classes were never
even imported into the transformer, so every one of these literals evaluated to None. A
developer writing a hex color, a percentage, or a pixel dimension got nothing, with no error.

Same shape as the uuid() bug: both ends of the feature were built (grammar + evaluator),
nobody connected them (no builder, no import). These are wired for mioimage and ai.generate.

These tests exist so the literals cannot silently revert to None.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import ColorLit, PercentLit, DimensionLit

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)


def ast_of(src):
    return transform(_P.parse(src), src)


def value_of(src):
    b = MohioInterpreter().run(ast_of(src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# --- the nodes get built (not None, not a bare Literal) --------------------------------

check("#ff8800 builds a ColorLit",
      isinstance(ast_of('hold c #ff8800\n').statements[0].value, ColorLit),
      "did not build ColorLit -- if None/Literal, the literal is unwired again")
check("50% builds a PercentLit",
      isinstance(ast_of('hold p 50%\n').statements[0].value, PercentLit))
check("12px builds a DimensionLit",
      isinstance(ast_of('hold d 12px\n').statements[0].value, DimensionLit))

# --- the value survives to runtime (the actual bug was None here) ----------------------
check("#ff8800 evaluates to its literal, not None",
      value_of('give back 200 (#ff8800)\n') == '#ff8800',
      f"got {value_of('give back 200 (#ff8800)')!r}")
check("50% evaluates to its literal, not None",
      value_of('give back 200 (50%)\n') == '50%',
      f"got {value_of('give back 200 (50%)')!r}")
check("12px evaluates to its literal, not None",
      value_of('give back 200 (12px)\n') == '12px',
      f"got {value_of('give back 200 (12px)')!r}")

# --- variants the terminals allow -----------------------------------------------------
check("short hex #0f0 works", value_of('give back 200 (#0f0)\n') == '#0f0',
      f"got {value_of('give back 200 (#0f0)')!r}")
check("8-digit hex #1a2b3c4d works",
      value_of('give back 200 (#1a2b3c4d)\n') == '#1a2b3c4d')
check("dpi unit 300dpi works", value_of('give back 200 (300dpi)\n') == '300dpi')
check("decimal percent 12.5% works", value_of('give back 200 (12.5%)\n') == '12.5%')

# --- survives being held and read back (the path a real app uses) ---------------------
_held = value_of('hold brand #1a73e8\ngive back 200 brand\n')
check("held color round-trips", _held == '#1a73e8', f"got {_held!r}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
