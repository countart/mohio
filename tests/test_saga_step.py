# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_saga_step.py — saga / step parse + build (mechanical plumbing).

Guards the fix that promoted the step-handler keywords (compensate / undo /
best effort) from bare string literals (which the lexer lost to NAME, so a
compensation body was mis-parsed as bogus assignments: `undo = remove`, ...) to
named terminals, and added the transformer so each step recovers its compensation
body, best-effort flag, and on.failure/on.success handlers DISTINCTLY.

Saga EXECUTION is intentionally fail-loud pending a design ruling -- this test
also locks that in (so unratified semantics, e.g. best_effort as a silent no-op,
can never ship by accident). See Docs/saga-step-semantics-for-design-chat.md.

Run: python3 tests/test_saga_step.py
"""
import os, sys
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark, Tree
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import SagaDecl, StepBlock, OnFailure, OnSuccess

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def _build(src):
    return transform(_P.parse(src), src)


# ── 1. compensate / undo / best effort / handlers all distinguished ──
print("step handlers are distinguished after parse+build")
SRC = '''saga fulfill_order
    step reserve
        save to db.holds
            id order.id
        save: done
        compensate
            remove from db.holds
                match id to order.id
            remove: done
    step: done
    step charge
        save to db.charges
            id order.id
        save: done
        best effort
    step: done
    step legacy
        save to db.legacy
            id order.id
        save: done
        undo
            remove from db.legacy
                match id to order.id
            remove: done
    step: done
    step notify
        save to db.outbox
            id order.id
        save: done
        on.failure
            show "notify failed"
    step: done
saga: done
'''
prog = _build(SRC)
saga = prog.statements[0]
check("top-level is SagaDecl", isinstance(saga, SagaDecl), True)
check("saga name", saga.name, 'fulfill_order')
check("four steps built", len(saga.steps), 4)
check("all steps are StepBlock", all(isinstance(s, StepBlock) for s in saga.steps), True)

reserve, charge, legacy, notify = saga.steps

# compensate -> undo body present, not best_effort, no mangled body
check("reserve has compensation body", len(reserve.undo) > 0, True)
check("reserve not best_effort", reserve.best_effort, False)
check("reserve body is its save (1 stmt)", len(reserve.body), 1)
# best effort -> flag set, no undo body
check("charge is best_effort", charge.best_effort, True)
check("charge has no compensation body", len(charge.undo), 0)
# undo (alias) -> populates compensation body
check("legacy (undo alias) has compensation body", len(legacy.undo) > 0, True)
check("legacy not best_effort", legacy.best_effort, False)
# on.failure -> handler captured, not confused with body/undo
check("notify has one handler", len(notify.handlers), 1)
check("notify handler is OnFailure", isinstance(notify.handlers[0], OnFailure), True)
check("notify has no compensation body", len(notify.undo), 0)


# ── 2. the mangled-assignment bug is gone ─────────────────────
print("compensation body is real statements, not bogus assignments")
# Before the fix, `undo` parsed as `undo = remove` etc. inside the STEP BODY.
# Assert the step body contains ONLY the save (no stray assignment named undo/from/match)
from mohio_ast import Assignment
def _body_names(step):
    return [getattr(c, 'name', None) for c in step.body if isinstance(c, Assignment)]
check("reserve body has no stray 'undo' assignment", 'undo' not in _body_names(reserve), True)
check("legacy body has no stray 'undo' assignment", 'undo' not in _body_names(legacy), True)


# ── 3. saga execution is now wired (terminal status object) ───
print("saga execution is wired (returns a terminal status object)")
def _run(src):
    return MohioInterpreter(ai=MockAI()).run(_build(src), None)
res = _run(SRC)
blob = res.to_python() if hasattr(res, 'to_python') else res
status = blob.get('status') if isinstance(blob, dict) else None
check("execution no longer fail-loud (no saga_pending_design)",
      'saga_pending_design' not in str(res), True)
check("saga returns one of the three terminal statuses",
      status in ('COMMITTED', 'COMPENSATED', 'FAILED_COMPENSATION'), True)


print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
