# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Pre-seal gate: an audit record carrying a raw value is refused BEFORE the write.

WHY THIS IS A HARD GATE AND NOT A TEST
An audit record can be sealed into storage that refuses deletion for the retention period. If a
bug puts a raw PHI or PCI value into one, that value cannot be removed afterwards -- not by the
tenant, not by the platform, not by a court order. It is a standing violation with no remediation
path for years.

Every other correctness failure in the audit system can be fixed and moved on from. This one
cannot. So it is checked before the write, it fails the write, and the refusal is never swallowed
-- not even for an app under no compliance framework, because a refusal is not an infrastructure
hiccup, it is code emitting protected data.

The rule: an audit trail records what happened -- field NAMES, ids, counts, classifications,
timestamps -- never the sensitive VALUES it exists to protect, so it can never become a second
unguarded copy of the regulated data.

Detection is deliberately narrow. A gate that cries wolf gets disabled, and a disabled gate
protects nothing. Card detection uses the Luhn checksum precisely so that timestamps, counts, and
ids do not trip it.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter, DbRuntime, Context

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def _fresh():
    it = MohioInterpreter(); db = DbRuntime(':memory:'); it._db = db
    class C(Context):
        def get_connection(self, _n): return db
    return it, db, C()


def _write(entry):
    """Returns 'wrote' | 'refused' | 'other'."""
    it, db, ctx = _fresh()
    try:
        it._audit_event('log', entry, ctx)
        return 'wrote'
    except MohioInterpreter.AuditContentRefused:
        return 'refused'
    except Exception:
        return 'other'


# ── legitimate records must NOT be blocked (a gate that cries wolf gets disabled) ─────
check("field names mapped to classifications are written",
      _write({'event': 'decision', 'agent': 'ai',
              'inputs': {'ssn': '[phi]', 'balance': '[pci]', 'region': '-'}}) == 'wrote')
check("counts and metrics are written",
      _write({'event': 'limit_breach', 'agent': 'payments',
              'steps': 4, 'tokens': 1200, 'cost': 0.42}) == 'wrote')
check("ISO timestamps do not trip the gate",
      _write({'event': 'x', 'agent': 'a',
              'when': '2026-07-19T03:22:11.123456Z'}) == 'wrote')
check("long numeric ids that are not card numbers do not trip the gate",
      _write({'event': 'x', 'agent': 'a', 'order_ref': '1234567890123'}) == 'wrote')

# ── the permanent-violation cases must be refused ─────────────────────────────────────
check("a raw email address is refused",
      _write({'event': 'decision', 'agent': 'ai',
              'inputs': {'email': 'patient@hospital.org'}}) == 'refused')
check("a raw US social security number is refused",
      _write({'event': 'decision', 'agent': 'ai',
              'inputs': {'ssn': '123-45-6789'}}) == 'refused')
check("a raw payment card number is refused (Luhn-valid)",
      _write({'event': 'decision', 'agent': 'ai',
              'inputs': {'card': '4111111111111111'}}) == 'refused')
check("a card number with separators is refused",
      _write({'event': 'd', 'agent': 'ai',
              'inputs': {'card': '4111 1111 1111 1111'}}) == 'refused')

# ── the refusal is never swallowed, at any tier ───────────────────────────────────────
# An app under no compliance framework may tolerate a transient sink failure. It may NOT
# tolerate this: the refusal means code emitted protected data, and the developer has to find
# out while the fix is still possible.
it, db, ctx = _fresh()
_propagated = False
try:
    it._audit_event('log', {'event': 'd', 'agent': 'ai',
                            'inputs': {'ssn': '123-45-6789'}}, ctx)
except MohioInterpreter.AuditContentRefused:
    _propagated = True
except Exception:
    pass
check("the refusal propagates rather than degrading to a warning", _propagated)
check("nothing was persisted when the record was refused",
      db.conn.execute("SELECT COUNT(*) c FROM log").fetchone()['c'] == 0
      if db.conn.execute(
          "SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name='log'"
      ).fetchone()['n'] else True)

# ── the gate sits at the single chokepoint every audit writer passes through ──────────
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio_interpreter.py'), encoding='utf-8').read()
check("the gate is called from the shared chained-save path (so every writer is covered)",
      '_audit_preseal_check(log_name, row)' in _src)
check("the refusal type is distinct from an ordinary write failure",
      'class AuditContentRefused' in _src)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
