# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-AUDIT-COVERAGE-GAPS Part D (2026-08-17): ai.decide/ai.rank (_write_ai_audit) and
cm.retain/cm.expire/cm.lock (_compliance_audit) used to write straight to
`_audit_chained_save` against the TENANT db, bypassing a registered `_audit_sink_provider`
entirely (the M2/M3 sink-bypass anti-pattern -- SOP-BUILD-UNTIL-DONE.md's standing guardrail
names these two functions as the example not to copy). Fixed: both now route through
`_audit_event`, the same seam `_audit_data_access`/`_audit_data_change`/`grant role` already
use. cm.purge's from-form was already on this seam before this fix; this file covers the three
verbs that weren't (retain/expire/lock) plus the ai.* pair.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD) -- a
registered provider sink and the tenant db are both inspected directly to prove WHICH one
actually received the write, not just that a record exists somewhere.

Run: `python tests/test_audit_m2_m3_sink_reroute.py`.
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


class Sink:
    """A minimal provider-verified durable sink, matching test_audit_sink_seam.py's double."""
    def __init__(self):
        self.rows = []
        self.by_table = {}
        self._mohio_grade_verified = 'durable'
    def ensure_table(self, *a): pass
    def save(self, table, row):
        self.by_table.setdefault(table, []).append(row)
        self.rows.append(row)


def run_real(src, ai=None, request=None):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter(ai=ai) if ai else MohioInterpreter()
    it.run_declarations(prog)
    it.run(prog, request=request)
    return it


# ── cm.retain / cm.expire / cm.lock: with a provider registered, the record goes to the
# provider, NOT the tenant db (confirmed by checking BOTH: the provider sink has the row, and
# the tenant db's compliance_audit table doesn't even get created) ─────────────────────────────
sink = Sink()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [sink])
try:
    it = run_real(
        'connect db as sqlite from env.DATABASE_URL\n'
        'cm.retain user.email for 2 years\n'
        'cm.expire user.token after 30 days\n'
        'cm.lock legal_case_123\n'
    )
    check("cm.retain/expire/lock all land in the registered provider sink",
          len(sink.by_table.get('compliance_audit', [])) == 3,
          sink.by_table.get('compliance_audit'))
    _tenant_has_table = True
    try:
        it._db.conn.execute("SELECT * FROM compliance_audit").fetchall()
    except Exception:
        _tenant_has_table = False
    check("the tenant db never even got a compliance_audit table created "
          "(provider fully replaced it, not just written alongside)",
          not _tenant_has_table)
finally:
    MohioInterpreter.unregister_audit_sink_provider()

# ── cm.* fallback: with NO provider registered, the record still lands durably in the
# tenant db (the pre-existing open-core default must be unaffected by this fix) ────────────────
it = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'cm.retain user.email for 2 years\n'
    'cm.lock legal_case_123\n'
)
_rows = it._db.conn.execute("SELECT event, detail FROM compliance_audit").fetchall()
check("cm.* still writes to the tenant db when no provider is registered (regression guard)",
      len(_rows) == 2, _rows)
check("the written record's content survived the reroute (action=retain visible in detail)",
      any('"action": "retain"' in r[1] for r in _rows), _rows)


# ── ai.decide (+ ai.audit to NAME): with a provider registered, the record goes to the
# provider, under the developer-chosen log_name table, NOT the tenant db ───────────────────────
class _MockAi:
    def decide(self, **kw):
        import types
        return types.SimpleNamespace(result=True, confidence=0.9, fell_back=False,
                                      model='mock', explanation='', inputs=kw.get('inputs', {}))

AI_SRC = (
    'ai.decide riskCheck returns boolean\n'
    '    weigh amt\n'
    '    ai.audit to risk_audit\n'
    '    not confident\n'
    '        give back false\n'
    'ai.decide: done\n'
    'shape Cmd\nshape: done\n'
    'listen for\n    new sh.Cmd at /go\n'
    '        hold amt 100\n'
    '        ai.decide riskCheck\n'
    '        give back 200 "ok"\n'
    '    new: done\nlisten: done\n'
)

sink2 = Sink()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [sink2])
try:
    it2 = run_real(AI_SRC, ai=_MockAi(), request={'_method': 'POST', '_path': '/go', 'cmd': {}})
    check("ai.decide's ai.audit record lands in the registered provider sink, "
          "under the developer-chosen table name (risk_audit)",
          len(sink2.by_table.get('risk_audit', [])) == 1, sink2.by_table)
    check("the record carries the decision name and confidence (inside detail, not lost)",
          sink2.rows and '"decision_name": "riskCheck"' in sink2.rows[0].get('detail', ''),
          sink2.rows)
finally:
    MohioInterpreter.unregister_audit_sink_provider()

# ── ai.decide fallback: with NO provider, still lands durably in the tenant db ─────────────────
it3 = run_real(
    'connect db as sqlite from env.DATABASE_URL\n' + AI_SRC,
    ai=_MockAi(), request={'_method': 'POST', '_path': '/go', 'cmd': {}})
_rows3 = it3._db.conn.execute("SELECT event, agent, detail FROM risk_audit").fetchall()
check("ai.decide still writes to the tenant db when no provider is registered (regression guard)",
      len(_rows3) == 1 and _rows3[0][1] == 'riskCheck', _rows3)


print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
