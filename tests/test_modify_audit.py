# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""A `modify` (bulk data change) must audit like its fail-loud siblings (save/remove), and must NOT
silently succeed if the audit write fails.

`modify` was the only data-change verb that wrapped its audit in `try: ... except Exception: pass`
-- a data change that could not be audited passed unrecorded. Same principle as cm.purge: a change
that cannot be proven happened must not report success. This locks:
  1. a modify CALLS the audit trail (operation='modify', the table, a row count).
  2. an audit-write failure RAISES rather than letting the modify pass unrecorded.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime

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

MODIFY = ('connect db as sqlite from env.DATABASE_URL\n'
          'save to db.items\n    name "a"\n    status "old"\nsave: done\n'
          'modify every item in db.items\n    apply item\n        status "new"\n'
          '    apply: done\nmodify: done\n')


print("=== 1. a modify CALLS the audit trail (like save/remove) ===")
calls = []
it = MohioInterpreter(); it._db = DbRuntime(':memory:')
def spy(op, table, ctx, **k):
    calls.append((op, table, k)); return None
it._audit_data_change = spy
it.run(transform(P.parse(MODIFY), MODIFY))
mod_calls = [(t, k) for (o, t, k) in calls if o == 'modify']
check("modify produced an audit call", len(mod_calls) >= 1, str(calls))
check("audit call names the table and a row count",
      any(t == 'items' and k.get('count', 0) >= 1 for (t, k) in mod_calls), str(mod_calls))

print("\n=== 2. an audit-write failure RAISES (modify not silently unrecorded) ===")
it2 = MohioInterpreter(); it2._db = DbRuntime(':memory:')
def boom(op, table, ctx, **k):
    if op == 'modify':
        raise Exception("simulated audit-write failure")
    return None
it2._audit_data_change = boom
raised = False
try:
    it2.run(transform(P.parse(MODIFY), MODIFY))
except Exception:
    raised = True
check("a failed modify-audit raises (was: silently swallowed)", raised)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
