# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`run NAME` task-invocation (run_block) is retired -- `call` is the canonical verb (Row 2, Tier 4,
2026-08-01).

The legacy `run_block` grammar rule (`RUN NAME run_body* closer | RUN NAME WITH ... | RUN NAME`)
was a training-wheels catcher whose transformer always failed loud with "use call". It is now
removed from the grammar; `run NAME` Earley-falls-back to an assignment named `run`, which the
assignment guard refuses with a clean "not a valid task invocation, use call NAME" -- so removal
does NOT regress into a raw parse error. `run` survives only for `run async ...` (a future
release, fails loud) and `run mioschedule.X now` (built).

This locks: every retired `run NAME` form fails loud with a `call` redirect; `call` works; the two
kept `run` forms are unaffected. Run: `python tests/test_run_block_retired.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_RAW = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def compile_err(src):
    try:
        transform(_P.parse(src), src)
        return None
    except Exception as e:
        return str(e)


def run_shown(src):
    try:
        it = MohioInterpreter()
        it.run(transform(_P.parse(src), src))
        return it.shown if getattr(it, 'shown', None) else []
    except Exception as e:
        return f"<error: {e}>"


TASK = 'task greet\n    take x as text default "hi"\n    show x\ntask: done\n'

# ── retired run-as-task forms: fail loud, redirect to `call` ────────────────────────────
for _label, _src in (
    ("run NAME (bare)",        TASK + 'run greet\n'),
    ("run NAME with value",    TASK + 'run greet with "yo"\n'),
    ("run NAME block + args",  TASK + 'run greet\n    x "yo"\nrun: done\n'),
):
    _e = compile_err(_src)
    check(f"{_label}: fails loud", _e is not None, _src)
    check(f"{_label}: names `call`", bool(_e) and 'call' in (_e or '').lower(), _e or '')
    check(f"{_label}: not silently run as `run greet` output",
          _e is not None, "must not execute")

# ── the canonical `call` still works ───────────────────────────────────────────────────
check("call greet runs the task", run_shown(TASK + 'call greet\n') == ['hi'])
check("call greet with value passes the arg",
      run_shown(TASK + 'call greet with "yo"\n') == ['yo'])

# ── the two KEPT `run` forms are unaffected ────────────────────────────────────────────
SCHED = ('task rec\n    show "SCHED-RAN"\ntask: done\n'
         'mioschedule s\n    every 1 days\n    run rec\nmioschedule: done\n'
         'run mioschedule.s now\n')
check("run mioschedule.NAME now still fires the task", run_shown(SCHED) == ['SCHED-RAN'])
check("run rec (single task inside a schedule) still parses",
      compile_err('task rec\n    show "x"\ntask: done\n'
                  'mioschedule s\n    every 1 days\n    run rec\nmioschedule: done\n') is None)

_ae = compile_err(TASK + 'run async greet\n')
check("run async still parses and fails loud (future release, NOT 'use call')",
      _ae is not None and 'future release' in _ae.lower() and 'not a valid task' not in _ae.lower(),
      _ae or '')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
