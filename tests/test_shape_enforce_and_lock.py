# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Shape-field type enforcement + `lock x` (lock an existing variable in place).

Two consistency fixes:

1. SHAPE-FIELD ENFORCEMENT. A shape field `age as int` is a type contract, exactly like a
   standalone `x as int`. Creating an instance with a mismatched value fails loud -- previously a
   shape field declared its type but never enforced it (it accepted `age "cat"` silently). This
   closes the gap so enforcement is consistent between standalone variables and shape fields.

2. `lock x` STANDALONE. Locking an already-existing variable in place now works. Previously
   `lock x` (no value) fell through to a no-op Assignment and silently did not lock -- only
   `lock x = 5` (declare-and-lock) worked. A lock that does not lock is a silent-wrong bug.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

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


def run(src):
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── shape-field type enforcement ──────────────────────────────────────────────────────
S = 'shape P\n    age as int\n    name as text\n    score as dec\nshape: done\n'
check("shape: valid int field accepted",
      run(S + 'create p as sh.P\n    age 25\ncreate: done\ngive back 200 p.age') == 25)
check("shape: text into an int field fails loud",
      fails(S + 'create p as sh.P\n    age "cat"\ncreate: done\ngive back 200 p.age'))
check("shape: int into a text field fails loud",
      fails(S + 'create p as sh.P\n    name 5\ncreate: done\ngive back 200 p.name'))
check("shape: text into a dec field fails loud",
      fails(S + 'create p as sh.P\n    score "high"\ncreate: done\ngive back 200 p.score'))
check("shape: int satisfies a dec field (widening ok)",
      run(S + 'create p as sh.P\n    score 5\ncreate: done\ngive back 200 p.score') == 5)
check("shape: partial instance (only some fields) is fine",
      run(S + 'create p as sh.P\n    age 30\ncreate: done\ngive back 200 p.age') == 30)
check("shape: standalone and shape enforcement are consistent (both reject text-in-int)",
      fails('x as int\nx "cat"\ngive back 200 x')
      and fails(S + 'create p as sh.P\n    age "cat"\ncreate: done\ngive back 200 p.age'))

# ── lock x standalone (lock an existing variable in place) ────────────────────────────
check("`lock x` seals an existing variable (later change fails)",
      fails('x 5\nlock x\nx 10\ngive back 200 x'))
check("`lock x` keeps the current value", run('x 5\nlock x\ngive back 200 x') == 5)
check("`lock x = 5` (declare-and-lock) still works",
      fails('lock x = 5\nx 10\ngive back 200 x'))
check("`lock x` on a missing variable fails loud (no silent no-op)",
      fails('lock nope\ngive back 200 "x"'))
check("a variable locked in place refuses the state operators too",
      fails('x 5\nlock x\nreplace x with 9\ngive back 200 x'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
