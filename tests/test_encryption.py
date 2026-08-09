# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Field-level encryption at rest — standalone (runs without pytest).

A shape field marked `sec.encrypt` is encrypted (AES-GCM) before storage and decrypted
transparently on read. At rest the value is ciphertext; without a key the save fails loud
rather than storing plaintext.

Key comes from env MOHIO_ENCRYPTION_KEY.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

SHAPE = ('shape Person\n    ssn as text [pii] sec.encrypt required\n'
         '    name as text\nshape: done\n')
DB = 'connect db as sqlite from env.DATABASE_URL\n'
SAVE = 'save to db.people\n    ssn "123-45-6789"\n    name "Ada"\nsave: done\n'


def run(src):
    return MohioInterpreter().run(transform(_P.parse(src), src)).get('body')


def _val(body):
    return body.to_python() if hasattr(body, 'to_python') else body


def test_encryption_roundtrip_and_at_rest():
    os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
    fails = []

    # 1. round-trip via retrieve -> plaintext back
    got = _val(run(SHAPE + DB + SAVE +
                   'retrieve p from db.people\n    match name to "Ada"\nretrieve: done\n'
                   'give back 200 p.ssn\n'))
    if str(got) != '123-45-6789':
        fails.append(f"retrieve round-trip: got {got!r}")

    # 2. round-trip via find
    got = _val(run(SHAPE + DB + SAVE +
                   'find ps in db.people\n    where name is "Ada"\nfind: done\n'
                   'give back 200 ps.first.ssn\n'))
    if str(got) != '123-45-6789':
        fails.append(f"find round-trip: got {got!r}")

    # 3. at rest -> ciphertext (raw sql sees enc:v1:)
    got = _val(run(SHAPE + DB + SAVE +
                   'retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n'))
    if not (isinstance(got, str) and got.startswith('enc:v1:')):
        fails.append(f"at-rest ciphertext: got {got!r}")

    # 4. non-encrypted field stays plaintext at rest
    got = _val(run(SHAPE + DB + SAVE +
                   'retrieve raw from db.*\n    sql\n        SELECT name FROM people\n    sql: done\nretrieve: done\ngive back 200 raw.first.name\n'))
    if str(got) != 'Ada':
        fails.append(f"non-encrypted at rest: got {got!r}")

    # 5. two different saves produce different ciphertext (random nonce)
    ct1 = _val(run(SHAPE + DB + SAVE +
                   'retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n'))
    ct2 = _val(run(SHAPE + DB + SAVE +
                   'retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n'))
    if ct1 == ct2:
        fails.append("ciphertext should differ per save (nonce)")

    assert not fails, "encryption failures:\n  " + "\n  ".join(fails)


def test_zone_seals_every_field():
    os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
    ZONE = ('shape Intake [phi]\n    ssn as text\n    dob as text\n    notes as text\nshape: done\n')
    SAVE = ('save to db.intakes\n    ssn "111-22-3333"\n    dob "1990-01-01"\n'
            '    notes "confidential"\nsave: done\n')
    fails = []
    # every field is ciphertext at rest
    for fld in ('ssn', 'dob', 'notes'):
        got = _val(run(ZONE + DB + SAVE +
                       f'retrieve raw from db.*\n    sql\n        SELECT {fld} FROM intakes\n    sql: done\nretrieve: done\ngive back 200 raw.first.{fld}\n'))
        if not (isinstance(got, str) and got.startswith('enc:v1:')):
            fails.append(f"zone field {fld} not sealed: {got!r}")
    # round-trips back to plaintext
    got = _val(run(ZONE + DB + SAVE +
                   'find rows in db.intakes\nfind: done\ngive back 200 rows.first.notes\n'))
    if str(got) != 'confidential':
        fails.append(f"zone round-trip: got {got!r}")
    assert not fails, "zone failures:\n  " + "\n  ".join(fails)


def test_generic_zone_seal_no_class():
    os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
    Z = 'shape Journal sec.encrypt\n    title as text\n    entry as text\nshape: done\n'
    S = 'save to db.journals\n    title "private"\n    entry "secret"\nsave: done\n'
    at_rest = _val(run(Z + DB + S + 'retrieve raw from db.*\n    sql\n        SELECT entry FROM journals\n    sql: done\nretrieve: done\ngive back 200 raw.first.entry\n'))
    back = _val(run(Z + DB + S + 'find rows in db.journals\nfind: done\ngive back 200 rows.first.entry\n'))
    assert isinstance(at_rest, str) and at_rest.startswith('enc:v1:'), f"generic zone not sealed: {at_rest!r}"
    assert str(back) == 'secret', f"generic zone round-trip: {back!r}"


def test_encryption_fails_loud_without_key():
    os.environ.pop('MOHIO_ENCRYPTION_KEY', None)
    body = run(SHAPE + DB + 'save to db.people\n    ssn "123-45-6789"\nsave: done\n'
               'give back 200 "stored"\n')
    text = str(_val(body))
    assert 'key_missing' in text or 'encryption' in text, \
        f"expected fail-loud without key, got {text!r}"


if __name__ == '__main__':
    passed = 0
    total = 4
    try:
        test_encryption_roundtrip_and_at_rest(); print("  [PASS] round-trip + at-rest + nonce"); passed += 1
    except AssertionError as e:
        print(f"  [FAIL] round-trip + at-rest: {e}")
    try:
        test_zone_seals_every_field(); print("  [PASS] zone seals every field"); passed += 1
    except AssertionError as e:
        print(f"  [FAIL] zone seals every field: {e}")
    try:
        test_generic_zone_seal_no_class(); print("  [PASS] generic zone seal (sec.encrypt)"); passed += 1
    except AssertionError as e:
        print(f"  [FAIL] generic zone seal: {e}")
    try:
        test_encryption_fails_loud_without_key(); print("  [PASS] fails loud without key"); passed += 1
    except AssertionError as e:
        print(f"  [FAIL] fails loud without key: {e}")
    print(f"\nRESULTS: {passed}/{total} passed")
    import sys; sys.exit(0 if passed == total else 1)
