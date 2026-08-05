# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
PHI audit-on-access (HIPAA): reading a [phi] field under an active sector writes a
DATA_ACCESS entry to the durable, hash-chained audit trail -- field NAMES only, never
values. HIPAA requires logging every ACCESS to PHI, not only every change. This mirrors
the write audit (_audit_data_change) for reads (find / retrieve / grab).

Rules verified:
1. field-level [phi] read under a sector -> one DATA_ACCESS entry naming the field
2. zone-level [phi] ([shape X [phi]]) read under a sector -> entry naming every field
3. no active sector -> no auto-audit (dev mode)
4. read that touches no [phi] field -> no entry
5. entries carry field NAMES only, never the values
6. grab path audits too
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

def _run(src):
    i = MohioInterpreter(); i.run(transform(_P.parse(src), src)); return i

def _acc(i):
    return [e for e in i._audit_logs.get('data_audit_log', [])
            if e.get('event') == 'DATA_ACCESS']

def _changes(i):
    return [e for e in i._audit_logs.get('data_audit_log', [])
            if e.get('event') == 'DATA_CHANGE']

CN = 'connect db as sqlite from env.DATABASE_URL\n'
SH = 'shape Patient\n    name as text\n    diagnosis as text [phi]\nshape: done\n'
SHNP = 'shape Patient\n    name as text\n    diagnosis as text\nshape: done\n'
SV = 'save to db.patients\n    name "Jane"\n    diagnosis "flu"\nsave: done\n'
FN = 'find p in db.patients\n    where name is "Jane"\nfind: done\n'
GR = 'grab p from db.patients\n    match name to "Jane"\ngrab: done\n'
ZONE = 'shape Intake [phi]\n    ssn as text\n    dob as text\nshape: done\n'
ZSV = 'save to db.intake\n    ssn "111"\n    dob "2000"\nsave: done\n'


def test_field_level_phi_read_audits():
    i = _run('sector: healthcare\n' + SH + CN + SV + FN)
    a = _acc(i)
    assert len(a) == 1, f"expected 1 DATA_ACCESS entry, got {len(a)}"
    assert a[0]['operation'] == 'find', a[0]
    assert a[0]['table'] == 'patients', a[0]
    assert a[0]['phi_fields'] == ['diagnosis'], a[0]
    assert a[0]['count'] == 1, a[0]

def test_zone_level_phi_read_audits():
    i = _run('sector: healthcare\n' + ZONE + CN + ZSV + 'find r in db.intake\nfind: done\n')
    a = _acc(i)
    assert len(a) >= 1, f"zone [phi] read did not audit: {a}"
    assert set(a[-1]['phi_fields']) == {'ssn', 'dob'}, a[-1]

def test_tag_carries_access_without_sector():
    # The [phi] tag carries audit-on-access with NO sector declared (default on).
    i = _run(SH + CN + SV + FN)
    a = _acc(i)
    assert len(a) == 1, f"[phi] read did not audit without a sector (tag must carry it): {len(a)}"
    assert a[0]['phi_fields'] == ['diagnosis'], a[0]

def test_tag_carries_write_audit_without_sector():
    # A write to a [phi] field is trailed with no sector (read + write symmetry).
    i = _run(SH + CN + SV)
    c = _changes(i)
    assert len(c) >= 1, "tagged write did not audit without a sector"
    assert c[-1]['operation'] == 'save', c[-1]

def test_non_tagged_write_no_sector_is_silent():
    # No sensitive field and no sector -> no auto-audit (dev).
    i = _run(SHNP + CN + SV)
    assert len(_changes(i)) == 0, "non-tagged write audited without a sector"

def test_non_phi_read_does_not_audit():
    i = _run('sector: healthcare\n' + SHNP + CN + SV + FN)
    assert len(_acc(i)) == 0, "non-[phi] read logged a PHI access entry"

def test_entries_are_names_only_never_values():
    i = _run('sector: healthcare\n' + SH + CN + SV + FN)
    blob = str(_acc(i))
    assert 'diagnosis' in blob, "field name missing from audit"
    assert 'flu' not in blob, "PHI VALUE leaked into the audit trail"

def test_grab_path_audits():
    i = _run('sector: healthcare\n' + SH + CN + SV + GR)
    a = _acc(i)
    assert len(a) >= 1, "grab of a [phi] field did not audit"
    assert a[-1]['operation'] == 'grab', a[-1]


def test_write_under_sector_audits_even_untagged():
    # A sector audits every write, tagged or not (compliance breadth, unchanged).
    i = _run('sector: financial\nshape Log\n    msg as text\nshape: done\n'
             + CN + 'save to db.logs\n    msg "x"\nsave: done\n')
    assert len(_changes(i)) >= 1, "sector did not audit an untagged write"

def test_tagged_table_remove_carries_without_sector():
    # Once a table is written with a sensitive field, later removes on it stay trailed
    # with no fields and no sector (table-level tag-carry via _tagged_tables).
    i = _run(SH + CN + SV + 'remove from db.patients\n    match name to "Jane"\nremove: done\n')
    ops = [e['operation'] for e in _changes(i)]
    assert 'save' in ops and 'remove' in ops, f"remove on a tagged table not trailed: {ops}"


# --- [pci] audit-on-access (PCI DSS req 10): reads of cardholder data are logged too ---
CARD = 'shape Card\n    ref as text\n    number as text [pci]\nshape: done\n'
CSV  = 'save to db.cards\n    ref "R1"\n    number "4111111111111234"\nsave: done\n'
CFN  = 'retrieve c from db.cards\n    match ref to "R1"\nretrieve: done\n'

def test_pci_read_is_access_logged():
    i = _run(CARD + CN + CSV + CFN + 'show c.number\n')
    a = _acc(i)
    assert any(e.get('pci_fields') == ['number'] for e in a), a

def test_pci_tag_carries_access_without_sector():
    i = _run(CARD + CN + CSV + CFN)     # no sector line at all
    a = _acc(i)
    assert any(e.get('pci_fields') == ['number'] for e in a), a

def test_phi_and_pci_in_one_row_both_logged():
    sh = 'shape Rec\n    name as text\n    dx as text [phi]\n    pan as text [pci]\nshape: done\n'
    sv = 'save to db.recs\n    name "A"\n    dx "flu"\n    pan "4111111111111234"\nsave: done\n'
    fn = 'retrieve r from db.recs\n    match name to "A"\nretrieve: done\n'
    i = _run('sector: healthcare\n' + sh + CN + sv + fn)
    a = _acc(i)
    assert a and a[0].get('phi_fields') == ['dx'] and a[0].get('pci_fields') == ['pan'], a


if __name__ == '__main__':
    tests = [
        (test_field_level_phi_read_audits,      "field-level [phi] read audits"),
        (test_zone_level_phi_read_audits,       "zone-level [phi] read audits"),
        (test_tag_carries_access_without_sector, "[phi] tag carries access-audit without a sector"),
        (test_tag_carries_write_audit_without_sector, "[phi] tag carries write-audit without a sector"),
        (test_non_tagged_write_no_sector_is_silent, "non-tagged write, no sector -> silent"),
        (test_non_phi_read_does_not_audit,      "non-[phi] read does not audit"),
        (test_entries_are_names_only_never_values, "entries are names-only, never values"),
        (test_grab_path_audits,                 "grab path audits"),
        (test_write_under_sector_audits_even_untagged, "sector audits every write (untagged too)"),
        (test_tagged_table_remove_carries_without_sector, "remove on a tagged table stays trailed"),
        (test_pci_read_is_access_logged,        "[pci] read is access-logged (PCI DSS req 10)"),
        (test_pci_tag_carries_access_without_sector, "[pci] tag carries access-audit without a sector"),
        (test_phi_and_pci_in_one_row_both_logged, "one read touching [phi] and [pci] logs both"),
    ]
    passed = 0
    for fn, label in tests:
        try:
            fn(); print(f"  [PASS] {label}"); passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {label}: {e}")
    print(f"\nRESULTS: {passed}/{len(tests)} passed")
    import sys; sys.exit(0 if passed == len(tests) else 1)
