#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for mioschedule wiring (register + on-demand trigger + run-due).

Model: declaring a mioschedule REGISTERS it (does not auto-fire — stateless
compute can't self-wake). `run mioschedule.NAME now` fires it immediately;
`run_due_schedules` is what an external driver (cron / dev ticker) calls.

Covers:
  1. Declaring registers the schedule and the task(s) it runs.
  2. `run mioschedule.NAME now` fires the task.
  3. run_due_schedules fires all registered schedules.
  4. Triggering an unregistered schedule fails loud.
  5. A schedule pointing at a missing task fails loud.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, MohioRuntimeError

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

DECL = ('task reconcile\n    show "reconcile ran"\ntask: done\n'
        'mioschedule daily_reconcile\n    every 1 days\n    run reconcile\n'
        'mioschedule: done\n')

def fresh(src):
    it = MohioInterpreter(); tree = transform(P.parse(src), src)
    it.run_declarations(tree); it.run(tree); return it, tree

# 1. registration
it, _ = fresh(DECL)
check("declaring registers the schedule + its task",
      it._schedules.get('daily_reconcile', {}).get('tasks') == ['reconcile'])

# 2. on-demand trigger fires the task
it, _ = fresh(DECL + 'run mioschedule.daily_reconcile now\n')
check("run mioschedule.NAME now fires the task", it.shown == ['reconcile ran'])

# 3. run_due_schedules fires all registered
it = MohioInterpreter(); tree = transform(P.parse(DECL), DECL)
it.run_declarations(tree); ctx = Context(); it._exec_declarations(tree, ctx)
fired = it.run_due_schedules(ctx)
check("run_due_schedules fires registered schedules",
      fired == ['daily_reconcile'] and it.shown == ['reconcile ran'])

# 4. unregistered trigger fails loud
it = MohioInterpreter(); ctx = Context()
loud = False
try:
    it._fire_schedule('nope', ctx)
except MohioRuntimeError as e:
    loud = 'no schedule named' in str(e).lower() or "nope" in str(e)
check("triggering an unregistered schedule fails loud", loud)

# 5. schedule pointing at a missing task fails loud
BADTASK = ('mioschedule s\n    every 1 days\n    run does_not_exist\n'
           'mioschedule: done\nrun mioschedule.s now\n')
loud2 = False
try:
    it2 = MohioInterpreter(); tr = transform(P.parse(BADTASK), BADTASK)
    it2.run_declarations(tr); it2.run(tr)
except MohioRuntimeError as e:
    loud2 = 'not defined' in str(e).lower() or 'does_not_exist' in str(e)
check("schedule referencing a missing task fails loud", loud2)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
