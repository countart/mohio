# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
PII purpose-bound flow, direct-use enforcement (GDPR Art. 5(1)(b) purpose limitation).

A [pii] field declares the purpose it was collected for:
    email as text [pii] purpose "account"
    phone as text [pii] purpose "account, support"
A `purpose "X" ... purpose: done` block asserts a use-purpose. A [pii] field referenced
DIRECTLY at a use (show) or egress (give back) point inside the block must have been
collected for X, or it fails loud. Enforcement is opt-in: no purpose block, no check
(backward compatible). Derived use (a variable holding the value) is the next block
(taint propagation) and is intentionally NOT enforced here.

Rules verified:
1. field-level `purpose` is registered per field (single and multi-purpose)
2. matching purpose allows the use and returns the real value
3. mismatched purpose blocks a `show`
4. mismatched purpose blocks a `give back`
5. a multi-purpose field is allowed under any of its declared purposes
6. a multi-purpose field is blocked under a purpose it was not collected for
7. no purpose scope -> no check (existing programs are unaffected)
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

SETUP = (
    'shape Customer\n'
    '    name as text\n'
    '    email as text [pii] purpose "account"\n'
    '    ad_id as text [pii] purpose "marketing"\n'
    '    phone as text [pii] purpose "account, support"\n'
    'shape: done\n'
    'connect db as sqlite from env.DATABASE_URL\n'
    'save to db.customers\n    name "Amy"\n    email "a@x.com"\n    ad_id "AD1"\n    phone "555"\nsave: done\n'
    'retrieve customer from db.customers\n    match name to "Amy"\nretrieve: done\n'
)

def _run(src):
    i = MohioInterpreter(); i.run(transform(_P.parse(src), src)); return i

def _blocked(tail):
    """True iff running SETUP+tail fails loud with the purpose-limitation error."""
    try:
        _run(SETUP + tail); return False
    except MohioRuntimeError as e:
        return 'purpose limitation' in str(e).lower()


def test_field_purpose_registered():
    i = _run(SETUP + 'show "x"\n')
    assert i._field_purposes.get('email') == {'account'}, i._field_purposes.get('email')
    assert i._field_purposes.get('ad_id') == {'marketing'}, i._field_purposes.get('ad_id')
    assert i._field_purposes.get('phone') == {'account', 'support'}, i._field_purposes.get('phone')

def test_match_allows_and_returns_value():
    i = _run(SETUP + 'purpose "account"\n    show customer.email\npurpose: done\n')
    assert i.shown and i.shown[-1].strip() == 'a@x.com', i.shown

def test_mismatch_blocks_show():
    assert _blocked('purpose "marketing"\n    show customer.email\npurpose: done\n')

def test_mismatch_blocks_giveback():
    assert _blocked('purpose "marketing"\n    give back 200 customer.email\npurpose: done\n')

def test_multi_purpose_allows_either():
    i = _run(SETUP + 'purpose "support"\n    show customer.phone\npurpose: done\n')
    assert i.shown and i.shown[-1].strip() == '555', i.shown

def test_multi_purpose_blocks_other():
    assert _blocked('purpose "marketing"\n    show customer.phone\npurpose: done\n')

def test_mismatch_blocks_miomail():
    assert _blocked('purpose "marketing"\n    miomail.send to customer.email subject "Hi" body "Yo"\npurpose: done\n')

def test_no_scope_no_check():
    i = _run(SETUP + 'show customer.email\n')
    assert i.shown and i.shown[-1].strip() == 'a@x.com', i.shown


def test_derived_copy_blocks():
    assert _blocked('purpose "marketing"\n    hold e customer.email\n    show e\npurpose: done\n')

def test_derived_copy_allows_and_returns():
    i = _run(SETUP + 'purpose "account"\n    hold e customer.email\n    show e\npurpose: done\n')
    assert i.shown and i.shown[-1].strip() == 'a@x.com', i.shown

def test_derived_concat_blocks():
    assert _blocked('purpose "marketing"\n    hold e customer.email\n    show ("Hi " & e)\npurpose: done\n')

def test_derived_giveback_blocks():
    assert _blocked('purpose "marketing"\n    hold e customer.email\n    give back 200 e\npurpose: done\n')

def test_derived_miomail_blocks():
    assert _blocked('purpose "marketing"\n    hold e customer.email\n    miomail.send to e subject "Hi" body "Yo"\npurpose: done\n')

def test_derived_concat_intersection():
    # A value built from two [pii] fields must satisfy BOTH: email(account) + phone(account,
    # support) -> only "account" works; "support" is blocked because email does not permit it.
    assert _blocked('purpose "support"\n    hold e customer.email\n    hold p customer.phone\n    show ("x" & e & p)\npurpose: done\n')
    i = _run(SETUP + 'purpose "account"\n    hold e customer.email\n    hold p customer.phone\n    show ("x" & e & p)\npurpose: done\n')
    assert i.shown, "account should satisfy both email and phone"


def _purpose_uses(i):
    return [(e['field'], e['purpose']) for e in i._audit_logs.get('data_audit_log', [])
            if e.get('event') == 'PURPOSE_USE']

def test_allowed_use_is_audited():
    i = _run(SETUP + 'purpose "account"\n    show customer.email\npurpose: done\n')
    assert _purpose_uses(i) == [('email', 'account')], _purpose_uses(i)

def test_derived_use_is_audited_with_field_name():
    i = _run(SETUP + 'purpose "account"\n    hold e customer.email\n    show e\npurpose: done\n')
    assert _purpose_uses(i) == [('email', 'account')], _purpose_uses(i)

def test_blocked_use_is_not_audited():
    i = MohioInterpreter()
    try:
        i.run(transform(_P.parse(SETUP + 'purpose "marketing"\n    show customer.email\npurpose: done\n'), SETUP))
    except MohioRuntimeError:
        pass
    assert _purpose_uses(i) == [], f"a blocked use must not be logged as a use: {_purpose_uses(i)}"


def test_for_purpose_show_blocks():
    assert _blocked('show customer.email for.purpose "marketing"\n')

def test_for_purpose_show_allows():
    i = _run(SETUP + 'show customer.email for.purpose "account"\n')
    assert i.shown and i.shown[-1].strip() == 'a@x.com', i.shown

def test_for_purpose_giveback_blocks():
    assert _blocked('give back 200 customer.email for.purpose "marketing"\n')

def test_for_purpose_miomail_blocks():
    assert _blocked('miomail.send to customer.email subject "Hi" body "Yo" for.purpose "marketing"\n')

def test_for_purpose_audited():
    i = _run(SETUP + 'show customer.email for.purpose "account"\n')
    assert _purpose_uses(i) == [('email', 'account')], _purpose_uses(i)


if __name__ == '__main__':
    tests = [
        (test_field_purpose_registered,       "field purpose registered (single + multi)"),
        (test_match_allows_and_returns_value,  "matching purpose allows use, returns value"),
        (test_mismatch_blocks_show,            "mismatched purpose blocks show"),
        (test_mismatch_blocks_giveback,        "mismatched purpose blocks give back"),
        (test_multi_purpose_allows_either,     "multi-purpose field allowed under any purpose"),
        (test_multi_purpose_blocks_other,      "multi-purpose field blocked under other purpose"),
        (test_mismatch_blocks_miomail,         "mismatched purpose blocks miomail.send (egress)"),
        (test_no_scope_no_check,               "no purpose scope -> no check"),
        (test_derived_copy_blocks,             "derived: copied field blocks on wrong purpose"),
        (test_derived_copy_allows_and_returns, "derived: copied field allowed on right purpose"),
        (test_derived_concat_blocks,           "derived: concat of a copy blocks"),
        (test_derived_giveback_blocks,         "derived: give back of a copy blocks"),
        (test_derived_miomail_blocks,          "derived: miomail of a copy blocks"),
        (test_derived_concat_intersection,     "derived: concat must satisfy every part (intersection)"),
        (test_allowed_use_is_audited,          "allowed use logged to the audit trail"),
        (test_derived_use_is_audited_with_field_name, "derived use logged with its source field name"),
        (test_blocked_use_is_not_audited,      "a blocked use is not logged as a use"),
        (test_for_purpose_show_blocks,         "for.purpose one-liner blocks a show"),
        (test_for_purpose_show_allows,         "for.purpose one-liner allows a show"),
        (test_for_purpose_giveback_blocks,     "for.purpose one-liner blocks a give back"),
        (test_for_purpose_miomail_blocks,      "for.purpose one-liner blocks a miomail"),
        (test_for_purpose_audited,             "for.purpose use is audited too"),
    ]
    passed = 0
    for fn, label in tests:
        try:
            fn(); print(f"  [PASS] {label}"); passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {label}: {e}")
        except Exception as e:
            print(f"  [ERR ] {label}: {type(e).__name__}: {e}")
    print(f"RESULTS: {passed}/{len(tests)} passed")
    import sys; sys.exit(0 if passed == len(tests) else 1)
