# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_saga_execution.py — saga EXECUTION semantics, per the ratified design ruling
(Docs/saga-step-semantics-for-design-chat.md).

Uses `show` markers (no DB) to observe ordering, and `raise` to fail a step.
Asserts the three terminal statuses and the exact compensation behavior:
  - all success                       -> COMMITTED
  - non-best-effort failure           -> COMPENSATED, completed steps undone in reverse
  - best-effort failure               -> COMMITTED (never downgraded), no compensation
  - a compensate itself fails         -> FAILED_COMPENSATION (rollback continues)
  - completed step has no compensate  -> FAILED_COMPENSATION
  - on.failure fires BEFORE compensation; on.success fires on success
  - failing step's own compensate never runs
  - nested saga                       -> fail loud

Run: python3 tests/test_saga_execution.py
"""
import os, sys
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def run_saga(src):
    """Run a top-level saga; return (status, shown_markers, steps_outcomes)."""
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    res = it.run(prog, None)
    blob = res.to_python() if hasattr(res, 'to_python') else res
    status = blob.get('status') if isinstance(blob, dict) else None
    steps = blob.get('steps') if isinstance(blob, dict) else None
    shown = [str(s) for s in it.shown]
    return status, shown, steps


# ── 1. all success -> COMMITTED ───────────────────────────────
print("all steps succeed -> COMMITTED")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        show "bf"\n'
    '        compensate\n            show "bc"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status COMMITTED", status, 'COMMITTED')
check("forward order, no compensation", shown, ['af', 'bf'])


# ── 2. non-best-effort failure -> COMPENSATED, reverse undo ───
print("non-best-effort failure -> COMPENSATED, reverse compensation")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        show "bf"\n'
    '        compensate\n            show "bc"\n'
    '    step: done\n'
    '    step c\n        raise "boom"\n'
    '        compensate\n            show "cc"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status COMPENSATED", status, 'COMPENSATED')
# a,b completed; c failed (its own compensate never runs); rollback is b then a
check("compensation runs in reverse, failing step not compensated",
      shown, ['af', 'bf', 'bc', 'ac'])


# ── 3. best-effort failure -> COMMITTED, no compensation ──────
print("best-effort failure -> COMMITTED (never downgraded)")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        raise "soft fail"\n'
    '        best effort\n'
    '    step: done\n'
    '    step c\n        show "cf"\n'
    '        compensate\n            show "cc"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status COMMITTED despite best-effort failure", status, 'COMMITTED')
check("saga continued past best-effort failure, no compensation", shown, ['af', 'cf'])
check("best-effort step marked best_effort_failed",
      any(o.get('step') == 'b' and o.get('outcome') == 'best_effort_failed' for o in (steps or [])), True)


# ── 4. a compensate itself fails -> FAILED_COMPENSATION ───────
print("compensate fails -> FAILED_COMPENSATION (rollback continues)")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        show "bf"\n'
    '        compensate\n            raise "compensate boom"\n'
    '    step: done\n'
    '    step c\n        raise "boom"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status FAILED_COMPENSATION", status, 'FAILED_COMPENSATION')
# rollback tries b (fails) then continues to a (succeeds)
check("rollback continued after a failed compensate", shown, ['af', 'bf', 'ac'])


# ── 5. completed step with no compensate -> FAILED_COMPENSATION ─
print("completed non-best-effort step with no compensate -> FAILED_COMPENSATION")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '    step: done\n'
    '    step b\n        raise "boom"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status FAILED_COMPENSATION (a had nothing to undo)", status, 'FAILED_COMPENSATION')


# ── 6. on.failure fires BEFORE compensation ───────────────────
print("on.failure fires before saga-level compensation")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        raise "boom"\n'
    '        on.failure\n            show "bof"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status COMPENSATED", status, 'COMPENSATED')
check("order: forward a, on.failure b (local), then compensate a", shown, ['af', 'bof', 'ac'])


# ── 7. on.success fires on a successful step ──────────────────
print("on.success fires immediately on success")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        on.success\n            show "aos"\n'
    '    step: done\n'
    'saga: done\n')
status, shown, steps = run_saga(src)
check("status COMMITTED", status, 'COMMITTED')
check("on.success ran after forward action", shown, ['af', 'aos'])


# ── 8. nested saga -> fail loud ───────────────────────────────
print("nested saga fails loud")
src = (
    'saga outer\n'
    '    step a\n'
    '        saga inner\n'
    '            step x\n                show "xf"\n'
    '            step: done\n'
    '        saga: done\n'
    '    step: done\n'
    'saga: done\n')
raised = False
try:
    run_saga(src)
except Exception as e:
    raised = 'nested' in str(e).lower()
check("nested saga raised a clear nested-not-supported error", raised, True)


# ── 9. saga result binds to its name (<name>.status / .steps) ─
print("saga result binds to the saga's name")
# After the saga runs, `s.status` and `s.steps` are readable in the enclosing scope.
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '    step: done\n'
    '    step b\n        show "bf"\n'
    '    step: done\n'
    'saga: done\n'
    'show s.status\n')
status, shown, steps = run_saga(src)
check("`show s.status` resolved the bound result", shown, ['af', 'bf', 'COMMITTED'])

# A failing saga binds COMPENSATED under its name too.
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        raise "boom"\n'
    '    step: done\n'
    'saga: done\n'
    'show s.status\n')
status, shown, steps = run_saga(src)
check("failing saga binds COMPENSATED under its name",
      shown[-1] if shown else None, 'COMPENSATED')


# ── 10. real caller pattern: check <saga>.status / when ... ───
print("caller branches on the saga status with check/when")
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '    step: done\n'
    'saga: done\n'
    'check s.status\n'
    '    when "COMMITTED"\n        show "committed-branch"\n'
    '    otherwise\n        show "other-branch"\n'
    'check: done\n')
status, shown, steps = run_saga(src)
check("check s.status routed to the COMMITTED branch",
      shown, ['af', 'committed-branch'])

# A rolled-back saga routes to its own branch.
src = (
    'saga s\n'
    '    step a\n        show "af"\n'
    '        compensate\n            show "ac"\n'
    '    step: done\n'
    '    step b\n        raise "boom"\n'
    '    step: done\n'
    'saga: done\n'
    'check s.status\n'
    '    when "COMPENSATED"\n        show "rolled-back-branch"\n'
    '    otherwise\n        show "other-branch"\n'
    'check: done\n')
status, shown, steps = run_saga(src)
check("check s.status routed to the COMPENSATED branch",
      shown[-1] if shown else None, 'rolled-back-branch')


print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
