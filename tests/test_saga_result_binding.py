# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Saga result binding in task scope (external audit doc2 #2).

A named saga must bind its terminal-status result object into the ENCLOSING task scope after
`saga: done`, so the task can branch on `<saga>.status` (COMMITTED / COMPENSATED /
FAILED_COMPENSATION) and read `<saga>.steps`. The audit reported this as an unbound-result gap;
verified on the real path it is bound. This guard locks it so it cannot silently regress.

Uses `raise` to force failure paths (no DB needed), mirroring test_saga_execution.
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


def val(b):
    return b.to_python() if hasattr(b, 'to_python') else b


def run(src):
    return val(MohioInterpreter().run(transform(P.parse(src), src)).get('body'))


# 1. COMMITTED: all steps hold -> status binds COMMITTED in the task, branch fires
committed = run('''task doWork returns text
    saga work_saga
        step one
            show "one"
        step: done
    saga: done
    check work_saga.status
        when "COMMITTED"
            give back "committed"
        otherwise
            give back "otherwise"
    check: done
task: done
hold result call doWork
give back 200 result
''')
check("COMMITTED status binds in task scope, branch fires", committed == 'committed', committed)


# 2. COMPENSATED: a non-best-effort step fails and a prior step HAS a compensate -> rolled back,
#    status binds COMPENSATED in the task
compensated = run('''task doWork returns text
    saga work_saga
        step one
            show "one"
            compensate
                show "undo one"
            compensate: done
        step: done
        step two
            raise "boom"
        step: done
    saga: done
    check work_saga.status
        when "COMMITTED"
            give back "committed"
        when "COMPENSATED"
            give back "compensated"
        otherwise
            give back "otherwise"
    check: done
task: done
hold result call doWork
give back 200 result
''')
check("COMPENSATED status binds in task scope, branch fires", compensated == 'compensated',
      compensated)


# 3. the bound object exposes .status directly (not just via check/when)
status_direct = run('''task doWork returns text
    saga work_saga
        step one
            show "one"
        step: done
    saga: done
    give back work_saga.status
task: done
hold result call doWork
give back 200 result
''')
check("`<saga>.status` reads directly as COMMITTED", status_direct == 'COMMITTED', status_direct)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
