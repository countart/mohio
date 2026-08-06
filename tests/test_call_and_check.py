# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_call_and_check.py — regression guards for two gaps the golden suite surfaced.

Gap 1: a bare `call X` (no block/closer, no `with`) used to silently mis-parse as an
       assignment named `call` (the task never ran). It now fails loud. The no-arg
       invocation is the block form `call X / call: done`, which works — and a saga
       inside a task binds its status so the caller can `check <saga>.status`.
Gap 2: `mio check` used to swallow transform-time errors (it ran transform inside a
       broad try/except). It now surfaces them (invalid retrieve modifier, closer
       mismatch, retired keyword) and exits non-zero.

Run: python3 tests/test_call_and_check.py
"""
import os, sys, subprocess, tempfile
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform, MohioCompileError
from mohio_interpreter import MohioInterpreter

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def _build(src):
    return transform(_P.parse(src), src)


# ── Gap 1: bare call parses and runs; block form also runs; saga binds in a task ──
print("Gap 1 — bare call runs the task; block form works")

# bare `call X` (no block/closer) is now a valid single-statement invocation that RUNS the task.
# (It previously mis-parsed as an assignment named `call` and silently did nothing; that bug is
# fixed, and the resolution was to make the bare form a real RunBlock, not to fail loud.)
def _bare_call_parses():
    _build('call do_work\n')
try:
    _bare_call_parses(); check("bare `call X` parses (no longer mis-parses)", True, True)
except MohioCompileError:
    check("bare `call X` parses (no longer mis-parses)", False, True)

# bare call actually runs the task
BARE = ('task do_work\n    show "ran"\ntask: done\n'
        'call do_work\n')
it0 = MohioInterpreter(ai=MockAI())
it0.run(_build(BARE))
check("bare `call X` runs the task", 'ran' in [str(s) for s in it0.shown], True)

# no-arg block form runs the task
HDR = 'shape P\n    method GET\nshape: done\n'
src = (HDR +
       'task do_work\n    show "ran"\ntask: done\n'
       'listen for\n    request for sh.P at /p\n'
       '        call do_work\n        call: done\n'
       '        give back ok "done"\n    request: done\nlisten: done\n')
it = MohioInterpreter(ai=MockAI())
it.run(_build(src), {'_method': 'GET', '_path': '/p'})
check("`call X / call: done` runs the task", 'ran' in [str(s) for s in it.shown], True)

# the thing the golden test thought was broken: a saga in a task binds its status,
# and the in-task caller can branch on it.
src = (HDR +
       'task do_work\n'
       '    saga w\n        step a\n            show "af"\n        step: done\n    saga: done\n'
       '    check w.status\n'
       '        when "COMMITTED"\n            show "C"\n'
       '        otherwise\n            show "OTHER"\n'
       '    check: done\n'
       'task: done\n'
       'listen for\n    request for sh.P at /p\n'
       '        call do_work\n        call: done\n'
       '        give back ok "done"\n    request: done\nlisten: done\n')
it = MohioInterpreter(ai=MockAI())
it.run(_build(src), {'_method': 'GET', '_path': '/p'})
shown = [str(s) for s in it.shown]
check("saga inside a task binds status; check branches COMMITTED", shown, ['af', 'C'])


# ── Gap 2: mio check surfaces transform errors (CLI) ──────────
print("Gap 2 — mio check surfaces transform-time errors")

def _check_exit(src_text):
    with tempfile.NamedTemporaryFile('w', suffix='.mho', dir='/tmp', delete=False) as f:
        f.write(src_text); path = f.name
    try:
        r = subprocess.run([sys.executable, 'mio.py', 'check', path],
                           cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, 'PYTHONPATH': ROOT})
        return r.returncode, (r.stdout + r.stderr)
    finally:
        try: os.remove(path)
        except OSError: pass

code, out = _check_exit('retrieve.bogus r from db.x\n    match id to 1\nretrieve.bogus: done\n')
check("mio check rejects retrieve.bogus (exit 1)", code, 1)
check("mio check names the bad modifier", 'retrieve.bogus' in out, True)

code, _ = _check_exit('retrieve.all r from db.x\n    match id to 1\nretrieve.all: done\n')
check("mio check passes valid retrieve.all (exit 0)", code, 0)

code, _ = _check_exit('retrieve.all r from db.x\n    match id to 1\nretrieve.one: done\n')
check("mio check catches a closer mismatch (exit 1)", code, 1)


print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
