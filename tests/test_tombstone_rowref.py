# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Tombstone row_ref rule (D2) -- identify WHICH row was erased, never reversibly.

Ratified rule: the row reference in a TOMBSTONE is the PK `id` in the clear (a surrogate key, not
personal data); ANY other match value is a per-deployment SALTED hash, never the raw value. The
salt (env MOHIO_AUDIT_SALT) is per-deployment and is NEVER stored beside the hash, so a
low-cardinality field (email, ssn) cannot be reversed even by someone holding the whole audit
store. A non-id purge with no salt configured FAILS LOUD before deleting -- we never erase a row we
cannot then tombstone.

Proven by running:
  A. id match          -> row_ref {kind:id, ref:<id>} in the clear.
  B. non-id match      -> row_ref {kind:hash, ref:HMAC(salt, table|field|value)}; the raw value and
                          the salt are BOTH absent from the tombstone; the hash recomputes.
  C. non-id, NO salt   -> fails loud, the row is NOT deleted, and no tombstone is written.
  D. helper unit       -> _tombstone_row_ref: id in clear; non-id hashed; non-id w/o salt raises.
"""
import os, sys, sqlite3, hashlib, hmac

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter, DbRuntime, MohioRuntimeError
from mohio_transformer_ast import transform as ast_transform

_raw = Path('mohio.lark').read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SALT = 'deployment-secret-xyz'

# The provider hands out the current case's dedicated audit sink.
_CURRENT = {'audit': None}
MohioInterpreter.register_audit_sink_provider(lambda ctx: [_CURRENT['audit']])

def run(src):
    _CURRENT['audit'] = DbRuntime(':memory:')
    interp = MohioInterpreter()
    err = None
    try:
        interp.run(ast_transform(P.parse(src), src))
    except Exception as e:
        err = e
    return interp, _CURRENT['audit'], err

def detail_of(audit):
    cur = audit.conn.cursor()
    try:
        cur.execute('SELECT "detail" FROM data_audit_log WHERE "event" = \'TOMBSTONE\'')
    except sqlite3.OperationalError:
        return None
    rows = cur.fetchall()
    return rows[0][0] if rows else None

SAVE = ('connect db as sqlite from env.DATABASE_URL\n'
        'save to db.members\n    id "M001"\n    email "alice@example.com"\nsave: done\n')

try:
    # --- A. id match: PK id kept in the clear -------------------------------------------
    os.environ.pop('MOHIO_AUDIT_SALT', None)
    _, audit, err = run(SAVE + 'cm.purge from db.members\n    match id to "M001"\n'
                               '    reason "erasure request"\ncm.purge: done\n')
    d = detail_of(audit) or ""
    check("A. id match needs no salt and writes a tombstone", err is None and d, str(err))
    check("A. row_ref is kind 'id' with the id in the clear",
          '"kind": "id"' in d and 'M001' in d, d)

    # --- B. non-id match: salted hash, never the raw value, salt never stored -----------
    os.environ['MOHIO_AUDIT_SALT'] = SALT
    _, audit, err = run(SAVE + 'cm.purge from db.members\n    match email to "alice@example.com"\n'
                               '    reason "erasure request"\ncm.purge: done\n')
    d = detail_of(audit) or ""
    expect = hmac.new(SALT.encode(), b'members|email|alice@example.com', hashlib.sha256).hexdigest()
    check("B. non-id match writes a tombstone", err is None and d, str(err))
    check("B. row_ref is kind 'hash'", '"kind": "hash"' in d, d)
    check("B. the raw match value 'alice@example.com' is NOT in the tombstone",
          'alice@example.com' not in d, d)
    check("B. the deployment salt is NOT stored in the tombstone", SALT not in d, d)
    check("B. the stored hash equals HMAC(salt, table|field|value) -- recomputable by the verifier",
          expect in d, f"expected {expect} in {d}")

    # --- C. non-id match with NO salt: fail loud BEFORE deleting ------------------------
    os.environ.pop('MOHIO_AUDIT_SALT', None)
    interp, audit, err = run(SAVE + 'cm.purge from db.members\n    match email to "alice@example.com"\n'
                                    '    reason "erasure request"\ncm.purge: done\n')
    loud = err is not None and 'salt' in str(err).lower() and 'MOHIO_AUDIT_SALT' in str(err)
    check("C. a non-id purge with no salt fails loud (names MOHIO_AUDIT_SALT)", loud, str(err))
    still_there = interp._db.conn.execute(
        'SELECT COUNT(*) FROM members WHERE id = "M001"').fetchone()[0]
    check("C. the row was NOT deleted (failed before erasing)", still_there == 1, str(still_there))
    check("C. no tombstone was written", detail_of(audit) is None)

    # --- D. helper unit -----------------------------------------------------------------
    it = MohioInterpreter()
    os.environ['MOHIO_AUDIT_SALT'] = SALT
    r_id = it._tombstone_row_ref('members', 'id', 'M001')
    check("D. helper: id -> kind 'id', value in clear",
          r_id == {'field': 'id', 'kind': 'id', 'ref': 'M001'}, str(r_id))
    r_h = it._tombstone_row_ref('members', 'email', 'alice@example.com')
    check("D. helper: non-id -> kind 'hash', HMAC of table|field|value",
          r_h['kind'] == 'hash' and r_h['ref'] == expect, str(r_h))
    os.environ.pop('MOHIO_AUDIT_SALT', None)
    raised = False
    try:
        it._tombstone_row_ref('members', 'email', 'x')
    except MohioRuntimeError:
        raised = True
    check("D. helper: non-id with no salt raises", raised)

finally:
    MohioInterpreter.unregister_audit_sink_provider()
    os.environ.pop('MOHIO_AUDIT_SALT', None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
