# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Structural coverage for the tombstone verifier's resolution paths (GAP-1 hardening).

Every KIND of row_ref a tombstone can carry must have its present-detection invariant tested in
`_tombstone_ref_present`. This test DERIVES the family of kinds from the code -- the
`_tombstone_row_ref` PRODUCER, the one place a kind is ever minted -- rather than listing cases by
hand, and asserts the SAME two invariants against each kind:

    present row  -> (True,  True)     a still-present erased row must read PRESENT
    absent value -> (False, True)     a genuinely gone row must read ABSENT

Why this file exists: mutation testing (S5-M1b, 2026-07-31) found `_tombstone_ref_present` had two
sibling paths (id, salted-hash) and only the id path's present-branch was tested. The identical
mutation -- report a still-present erased row as gone, a FALSE HIPAA/GDPR erasure attestation --
was caught on id and MISSED on hash. A hand-written per-case test leaves the NEXT kind silently
uncovered. This makes that class impossible: add a kind to the producer and this test either
already covers it or FAILS THE BUILD demanding a case. The end-to-end sweep path is covered by
test_tombstone_verifier.py; this is the per-kind structural guard that complements it.

Run as a script: `python tests/test_tombstone_verifier_paths.py` (exit 0 = pass).
"""
import os, sys, re, inspect

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ['DATABASE_URL'] = ':memory:'

from mohio_interpreter import MohioInterpreter, DbRuntime

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SALT = 'deployment-secret-xyz'
os.environ['MOHIO_AUDIT_SALT'] = SALT

# --- the family, derived from the PRODUCER's source (never hand-listed) ---------------------
# `_tombstone_row_ref` is the ONLY place a kind is minted; every kind it can emit is a resolution
# path the verifier must handle. A new `'kind': 'X'` added there appears here automatically.
producer_src = inspect.getsource(MohioInterpreter._tombstone_row_ref)
FAMILY = set(re.findall(r"'kind':\s*'([a-z_]+)'", producer_src))
check("derived a non-empty kind family from the producer source", len(FAMILY) >= 1, str(FAMILY))
print(f"    producer emits kinds: {sorted(FAMILY)}")

# --- a live tenant store with a present and an absent row for each field type ---------------
interp = MohioInterpreter()
tenant = DbRuntime(':memory:')
tenant.save('members', {'id': 'K1', 'email': 'present@x.com'})   # the "still present" row
# (no row for id 'GONE' / email 'gone@x.com' -> those are the "absent" cases)

# For each kind, a builder returns (present_ref, absent_ref), each MINTED BY THE REAL PRODUCER so
# the kind is decided by production code, not asserted here. A new kind with no builder trips the
# completeness gate below rather than being silently skipped.
REF_BUILDERS = {
    'id':   lambda: (interp._tombstone_row_ref('members', 'id', 'K1'),
                     interp._tombstone_row_ref('members', 'id', 'GONE')),
    'hash': lambda: (interp._tombstone_row_ref('members', 'email', 'present@x.com'),
                     interp._tombstone_row_ref('members', 'email', 'gone@x.com')),
}

# --- anti-rot completeness gate: no producer kind may lack a coverage builder ---------------
uncovered = FAMILY - set(REF_BUILDERS)
check("every producer kind has a present/absent coverage builder (add one for a new kind)",
      not uncovered,
      f"producer emits {sorted(uncovered)} but this test has no invariant case for it -- add one")

# --- the two invariants, asserted against EVERY kind ---------------------------------------
for kind in sorted(FAMILY & set(REF_BUILDERS)):
    present_ref, absent_ref = REF_BUILDERS[kind]()
    check(f"[{kind}] builder actually mints this kind", present_ref.get('kind') == kind, str(present_ref))
    # PRESENT invariant -- the branch S5-M1b flipped on the hash path.
    got_present = interp._tombstone_ref_present(tenant, 'members', present_ref)
    check(f"[{kind}] a STILL-PRESENT erased row reads present -> (True, True)",
          got_present == (True, True), str(got_present))
    # ABSENT invariant -- a genuinely gone row must read absent, never a false 'present'.
    got_absent = interp._tombstone_ref_present(tenant, 'members', absent_ref)
    check(f"[{kind}] a genuinely absent row reads absent -> (False, True)",
          got_absent == (False, True), str(got_absent))

os.environ.pop('MOHIO_AUDIT_SALT', None)
print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
