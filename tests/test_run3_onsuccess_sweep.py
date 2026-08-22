# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""RUN 3 Part I4/B3 (2026-08-19): on.success coverage sweep -- the STATE channel's other pole,
same class of risk as on.failure (which RUN 1/2 already swept). Found by grepping every
`isinstance(h, OnSuccess)` dispatch site and checking whether it routes through the shared
`_handle_success` (which runs on.success AND when/otherwise) or a narrower, ad hoc loop.

FIVE gaps found, all fixed the same way (swap the narrow loop for `self._handle_success`):
  - pull: on.success-only, when/otherwise never dispatched.
  - save ... unless X exists, the "duplicate found, skipped" branch specifically (the normal
    insert branch of the same block already used _handle_success correctly -- only the
    early-return dedupe-hit path was narrow).
  - save all: on.success-only, when/otherwise never dispatched.
  - remove.all: on.success-only, when/otherwise never dispatched.
  - save or update / upsert: ZERO handler dispatch at all -- worse than narrow, not even
    on.success fired.

NOT touched, investigated and found to be correctly OUT of scope:
  - check unique: already has its own correct, deliberately narrower when-empty/otherwise
    dispatch (T1-CHECK-UNIQUE-REDESIGN, 2026-08-11, predates this run) -- not a gap.
  - check count: on.success-only is unchanged; no natural CONDITION for a raw count.
  - validate / check X against Y: on.failure/on.success there are VALIDATION-RESULT flags
    (did the data pass), not operational STATE flags -- a different, intentional semantic,
    not a connection/query verb, per I2's "don't bolt handlers where they don't apply."

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_run3_onsuccess_sweep.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
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


def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    it.run(prog)
    return it


SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'shape Thing\n    name as text\nshape: done\n'
        'save to db.things\n    name "widget"\nsave: done\n')

# ── pull: when/otherwise now dispatch (was on.success-only) ────────────────────────────────
it = run_real(SEED +
    'pull got up to 5 from db.things\n'
    '    when got is.empty\n        show "PULL-EMPTY"\n    otherwise\n        show "PULL-HIT"\n'
    'pull: done\n')
check("pull's when/otherwise now dispatch", it.shown == ["PULL-HIT"], it.shown)

# ── save ... unless exists, duplicate-found branch: when/otherwise now dispatch ────────────
it = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Flag\n    k as text\nshape: done\n'
    'save to db.flags unless k exists\n    k "a"\nsave: done\n'
    'save to db.flags unless k exists\n    k "a"\n'
    '    when true\n        show "SAVE-DUP-DISPATCHED"\n'
    'save: done\n')
check("save-unless-exists duplicate-found branch now dispatches when/otherwise",
      it.shown == ["SAVE-DUP-DISPATCHED"], it.shown)

# ── save all: when/otherwise now dispatch (was on.success-only); empty batch is a legitimate,
# dict-vacuous input that proves dispatch without needing record-shaped list-literal syntax ──
it = run_real(SEED +
    'items as list text\n'
    'save all to db.batch from items\n'
    '    when true\n        show "BATCH-DISPATCHED"\n'
    'save: done\n')
check("save all now dispatches when/otherwise", it.shown == ["BATCH-DISPATCHED"], it.shown)

# ── remove.all: when/otherwise now dispatch (was on.success-only) ──────────────────────────
it = run_real(SEED +
    'remove.all from db.things\n'
    '    when true\n        show "REMOVEALL-DISPATCHED"\n'
    'remove.all: done\n')
check("remove.all now dispatches when/otherwise", it.shown == ["REMOVEALL-DISPATCHED"], it.shown)

# ── save or update / upsert: on.success now fires at all (was ZERO dispatch) ───────────────
it = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape U\n    id as text\n    name as text\nshape: done\n'
    'save or update db.upsert_things match id to "1"\n    name "Alice"\n'
    '    on.success\n        show "UPSERT-OK"\n'
    'save: done\n')
check("save or update now dispatches on.success (was zero dispatch of any handler)",
      it.shown == ["UPSERT-OK"], it.shown)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
