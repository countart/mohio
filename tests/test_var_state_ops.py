# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The five variable state-change operators: release / clear / forget / rename / replace.

A variable is a name + a type contract + a value, plus its existence. The five operators each act
on a different axis, and together are the ENTIRE vocabulary for changing a variable's state:

    release  -> the CONTRACT   (typed -> bare, keep value; also unfreezes a hold)
    clear    -> the VALUE      (value -> empty/type-zero, keep name + contract)
    replace  -> the VALUE      (swap for a new value; must satisfy any contract)
    rename   -> the NAME       (relabel; carry value + contract to the new name)
    forget   -> EXISTENCE      (remove the name entirely)

None of these silently no-ops: a clear/forget/rename/replace on a name that isn't there fails loud.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

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
        run(src); return False
    except Exception:
        return True


# ── release: drops a type contract, keeps the value (also still unfreezes a hold) ─────
check("release drops a type contract (x becomes malleable)",
      run('x as int\nx 5\nrelease x\nx "cat"\ngive back 200 x') == "cat")
check("release keeps the value when dropping the contract",
      run('x as int\nx 5\nrelease x\ngive back 200 x') == 5)
check("release still unfreezes a hold",
      run('hold x = 5\nrelease x\nx 10\ngive back 200 x') == 10)
check("release on a bare variable fails loud (nothing to release)",
      fails('y 5\nrelease y\ngive back 200 y'))

# ── clear: empties the value, keeps the name + contract ───────────────────────────────
check("clear empties a typed variable to its type-zero",
      run('x as int\nx 5\nclear x\ngive back 200 x') == 0)
check("clear KEEPS the type contract (x \"cat\" still fails after clear)",
      fails('x as int\nx 5\nclear x\nx "cat"\ngive back 200 x'))
check("clear on a bare variable empties it (to null)",
      run('y 5\nclear y\ngive back 200 y') is None)
check("clear on a missing name fails loud", fails('clear nope\ngive back 200 "x"'))

# ── forget: removes the name entirely ─────────────────────────────────────────────────
check("forget removes the variable (reads as empty after)",
      run('x 5\nforget x\ngive back 200 x') is None)
check("forget frees the name for a fresh declaration of any type",
      run('x "5"\nforget x\nx as int\nx 5\ngive back 200 x') == 5)
check("forget on a missing name fails loud", fails('forget nope\ngive back 200 "x"'))

# ── rename: relabel, carry value + contract ───────────────────────────────────────────
check("rename carries the value to the new name",
      run('x 5\nrename x to y\ngive back 200 y') == 5)
check("rename removes the old name",
      run('x 5\nrename x to y\ngive back 200 x') is None)
check("rename carries the type contract (new name still enforces)",
      fails('x as int\nx 5\nrename x to y\ny "cat"\ngive back 200 y'))
check("rename onto an existing name fails loud (never overwrites)",
      fails('x 5\ny 9\nrename x to y\ngive back 200 y'))
check("rename a missing name fails loud", fails('rename nope to y\ngive back 200 "x"'))

# ── replace: swap the value, respect the contract ─────────────────────────────────────
check("replace swaps the value", run('x 5\nreplace x with 10\ngive back 200 x') == 10)
check("replace respects a type contract (int cannot be replaced with text)",
      fails('x as int\nx 5\nreplace x with "cat"\ngive back 200 x'))
check("replace a valid typed value works",
      run('x as int\nx 5\nreplace x with 99\ngive back 200 x') == 99)
check("replace a missing variable fails loud", fails('replace nope with 1\ngive back 200 "x"'))

# ── the full design-session flow ──────────────────────────────────────────────────────
check("full flow: x \"5\" / forget x / x as int / x 5 -> 5",
      run('x "5"\nforget x\nx as int\nx 5\ngive back 200 x') == 5)
check("full flow: ...then x \"6\" fails loud",
      fails('x "5"\nforget x\nx as int\nx 5\nx "6"\ngive back 200 x'))

# ── lock is permanent: NO operator may clear/forget/rename/replace a locked var ───────
check("clear refuses a locked var (lock is permanent)", fails('lock x = 5\nclear x\ngive back 200 x'))
check("forget refuses a locked var", fails('lock x = 5\nforget x\ngive back 200 x'))
check("rename refuses a locked var", fails('lock x = 5\nrename x to y\ngive back 200 y'))
check("replace refuses a locked var", fails('lock x = 5\nreplace x with 9\ngive back 200 x'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
