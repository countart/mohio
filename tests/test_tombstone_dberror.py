# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Tombstone verifier must FAIL LOUD / UNVERIFIABLE on a DB read error, never a silent false-clean.

The audit guarantee is what the product is sold on. If a DB error while checking whether a row is
still present were read as "row absent," a real failure would silently pass as a CONFIRMED lawful
erasure (verify_tombstones ok) or a definite ERASED/MISSING verdict (adjudicate_erasure). That is
the same false-clean-result class as the cm.purge write-side bug, on the READ side. This locks:
  - verify_tombstones with an unreadable data store -> ok is False and the ref is UNVERIFIABLE.
  - adjudicate_erasure with an unreadable data store -> verdict UNVERIFIABLE (not ERASED/MISSING).
  - adjudicate_erasure with an unreadable audit store -> verdict UNVERIFIABLE (not MISSING).
  - the happy path (no error) still adjudicates correctly.
"""
import os, sys, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ['MOHIO_AUDIT_SALT'] = 'test-salt'

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


class _BrokenConn:
    def cursor(self):            raise sqlite3.OperationalError("database is locked")
    def execute(self, *a, **k):  raise sqlite3.OperationalError("database is locked")
    def rollback(self):          pass
class BrokenSink:
    def __init__(self):          self.conn = _BrokenConn()


# Build a REAL tombstone: seed a row, purge it (id match -> id-kind row_ref).
audit = DbRuntime(':memory:')
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    interp = MohioInterpreter()
    tenant = DbRuntime(':memory:'); interp._db = tenant
    src = ('connect db as sqlite from env.DATABASE_URL\n'
           'save to db.members\n    id "M001"\n    email "a@x.com"\nsave: done\n'
           'cm.purge from db.members\n    match id to "M001"\n    reason "erasure"\ncm.purge: done\n')
    interp.run(transform(P.parse(src), src))

    # --- sanity: happy path still adjudicates correctly ---
    rep = interp.verify_tombstones(audit, tenant)
    check("sanity: clean verify is ok", rep['ok'] is True, str(rep))
    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M001')
    check("sanity: clean adjudicate -> ERASED", v['verdict'] == 'ERASED', str(v))

    # --- DB error reading the DATA store: must be UNVERIFIABLE, never 'confirmed erased' ---
    rep2 = interp.verify_tombstones(audit, BrokenSink())
    check("data-store error -> verify NOT ok (never silent-clean)", rep2['ok'] is False, str(rep2))
    check("data-store error -> ref is UNVERIFIABLE (not counted as an absent/consistent row)",
          len(rep2['unverifiable']) >= 1 and not rep2['inconsistent'], str(rep2))

    v2 = interp.adjudicate_erasure(audit, BrokenSink(), 'members', 'id', 'M001')
    check("data-store error -> adjudicate UNVERIFIABLE (not ERASED/MISSING)",
          v2['verdict'] == 'UNVERIFIABLE', str(v2))

    # --- DB error reading the AUDIT store: must be UNVERIFIABLE, never 'no tombstone -> MISSING' ---
    v3 = interp.adjudicate_erasure(BrokenSink(), tenant, 'members', 'id', 'M001')
    check("audit-store error -> adjudicate UNVERIFIABLE (not MISSING)",
          v3['verdict'] == 'UNVERIFIABLE', str(v3))

    # --- an EMPTY audit (log never written) is not an error: a missing log means no tombstone,
    #     so an absent row is MISSING, not UNVERIFIABLE. (Refinement of the read-error fix.) ---
    empty_audit = DbRuntime(':memory:')
    v4 = interp.adjudicate_erasure(empty_audit, tenant, 'members', 'id', 'M001')
    check("empty audit log -> adjudicate MISSING (not UNVERIFIABLE)",
          v4['verdict'] == 'MISSING', str(v4))

finally:
    MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
