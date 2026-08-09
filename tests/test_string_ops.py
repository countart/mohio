# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Source-first string adjectives (B1): `name uppercase`/`lowercase` follow the noun
and transform; verb-first `uppercase name` must not lead. Plus two-word `starts with`/
`ends with` conditions (C2)."""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_passed = _failed = 0
def check(label, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  [PASS] {label}")
    else:    _failed += 1; print(f"  [FAIL] {label}")

def shown(body):
    it = MohioInterpreter()
    it.run(transform(_P.parse(body), body))
    return it.shown[-1] if getattr(it, 'shown', None) else None

def parses(body):
    try:
        transform(_P.parse(body), body); return True
    except Exception:
        return False

# B1: source-first adjective composes and transforms
check("name uppercase -> ARIA",  shown('hold name "Aria"\nshow name uppercase\n') == "ARIA")
check("name lowercase -> aria",  shown('hold name "ARIA"\nshow name lowercase\n') == "aria")
check("uppercase in a condition", shown(
    'hold name "aria"\ncheck name\n    when name uppercase is "ARIA"\n        show "M"\n    otherwise\n        show "N"\ncheck: done\n') == "M")
check("trim still works",         shown('hold s "  hi  "\nshow s trim\n') == "hi")
# verb-first must not lead (adjective can't open)
def fails_check(body):
    try:
        transform(_P.parse(body), body); return False
    except Exception:
        return True
check("verb-first `uppercase name` fails loud", fails_check('hold name "a"\nuppercase name\n'))

# C2: two-word conditions
check("two-word `starts with`", shown(
    'hold n "Aria"\ncheck n\n    when n starts with "Ar"\n        show "Y"\n    otherwise\n        show "X"\ncheck: done\n') == "Y")
check("two-word `ends with`", shown(
    'hold n "Aria"\ncheck n\n    when n ends with "ia"\n        show "Y"\n    otherwise\n        show "X"\ncheck: done\n') == "Y")
check("dotted `starts.with` still works", shown(
    'hold n "Aria"\ncheck n\n    when n starts.with "Ar"\n        show "Y"\n    otherwise\n        show "X"\ncheck: done\n') == "Y")

# A1: source-first replace (scoped argument tool)
check("verb-first replace in place", shown('r "abcabc"\nreplace "b" with "X" in r\nshow r\n') == "aXcaXc")
check("replace as-capture (source unchanged)", shown('r "hello world"\nreplace "world" with "there" in r as g\nshow g\n') == "hello there")

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
