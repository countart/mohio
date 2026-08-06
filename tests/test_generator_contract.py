# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Guard: `unique.id` and `random.uuid` produce a fresh value on EVERY read.

The contract, written into the grammar when unique.id was built: "a fresh, distinct
value on EVERY read, including twice on one line."

It was not true. `concat_term` is an ALLOWLIST:

    concat_term: STRING | dotted_name | template_str | literal | cast_expr

`random_expr` was reachable from `value_expr` but NOT from here. So inside a `&` the
only parse left for `unique.id` was `dotted_name` -- a VARIABLE lookup. It resolved to
nothing, and the generator silently produced empty string:

    hold s ("x" & unique.id)        -> "x"          (the id vanished)
    hold s (unique.id & "|" & unique.id) -> "|"     (both vanished)

No error. `mio check` was clean. This is the transformer/grammar allowlist disease: a
construct the list does not name does not fail -- it silently becomes something else.

Both generators had it. `unique.id` inherited it from `random.uuid`, which it mirrors.
It matters most exactly where these are used: audit trails and trace IDs.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def shown(src):
    it = MohioInterpreter(); it.run(transform(_P.parse(src), src))
    return str(it.shown[-1]) if getattr(it, 'shown', None) else None

def node_types(src):
    out = []
    def walk(n, d=0):
        if d > 10 or not hasattr(n, '__dict__'): return
        out.append(type(n).__name__)
        for v in vars(n).values():
            if hasattr(v, '__dict__'): walk(v, d + 1)
            elif isinstance(v, list):
                for i in v: walk(i, d + 1)
    prog = transform(_P.parse(src), src)
    for s in prog.statements: walk(s)
    return out

print("generator contract: unique.id / random.uuid are fresh on every read")

for gen in ('unique.id', 'random.uuid'):
    # 1. ROOT CAUSE. In a concat it must stay a RandomValue. If it comes through as a
    #    DottedName the allowlist hole is back and the value will silently be empty.
    types = node_types(f'hold s ("x" & {gen})\n')
    check(f"{gen} in a concat is a RandomValue, not a DottedName",
          'RandomValue' in types)
    check(f"{gen} in a concat did not degrade to a variable lookup",
          'DottedName' not in types)

    # 2. It actually produces a value in each position.
    check(f"{gen} bare is non-empty",            bool(shown(f'hold a {gen}\nshow a\n')))
    check(f"{gen} in a concat is non-empty",
          (shown(f'hold s ("x" & {gen})\nshow s\n') or "x") != "x")

    # 3. THE REPORTED BUG: twice on one line. Printed just "|" -- both reads empty.
    two = shown(f'hold s ({gen} & "|" & {gen})\nshow s\n') or ""
    left, _, right = two.partition("|")
    check(f"{gen} twice on one line: both reads produced a value",
          bool(left) and bool(right))
    check(f"{gen} twice on one line: the two reads are DISTINCT",
          bool(left) and left != right)

    # 4. Separate statements stay distinct (this always worked -- keep it that way).
    pair = shown(f'hold a {gen}\nhold b {gen}\nshow (a & "|" & b)\n') or ""
    l2, _, r2 = pair.partition("|")
    check(f"{gen} in two separate holds is distinct", bool(l2) and l2 != r2)

    # 5. A generator is a GENERATOR, not an identity: re-reading gives a new value.
    same = shown(f'hold a {gen}\nshow (a & "|" & a)\n') or ""
    l3, _, r3 = same.partition("|")
    check(f"{gen} held in a variable is STABLE when re-read (it is a value now)",
          bool(l3) and l3 == r3)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
