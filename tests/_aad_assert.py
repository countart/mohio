# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""What a stored protected value must be since T1-ENCRYPTION-AAD: sealed AND bound to its place.

A test that only asks `startswith("enc:v2:")` checks a label. This asks the property the label claims:
the value opens under the key for exactly its table, column and row, and does not open for a
neighbouring row or with no place at all. A wrong key fails the first test, so a value that merely
does not open is never mistaken for a bound one.
"""
import base64
import os


def _key_for(key_text):
    from mohio_interpreter import MohioInterpreter
    old = os.environ.get("MOHIO_ENCRYPTION_KEY")
    os.environ["MOHIO_ENCRYPTION_KEY"] = key_text
    try:
        return MohioInterpreter()._encryption_key()
    finally:
        if old is None:
            os.environ.pop("MOHIO_ENCRYPTION_KEY", None)
        else:
            os.environ["MOHIO_ENCRYPTION_KEY"] = old


def bound_reason(value, table, column, row_key, key_text="testkey", plain=None):
    """'' when `value` is a v2 field value sealed for (table, column, row_key) under `key_text` (and,
    given `plain`, opens to it); otherwise why not."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from mohio_interpreter import MohioInterpreter
    if not isinstance(value, str) or not value.startswith("enc:v2:"):
        return "not a v2 sealed value: %r" % (str(value)[:40],)
    key = key_text if isinstance(key_text, bytes) else _key_for(key_text)   # raw bytes: a provider's key
    blob = base64.b64decode(value[len("enc:v2:"):])
    nonce, ct = blob[:12], blob[12:]
    try:
        got = AESGCM(key).decrypt(nonce, ct, MohioInterpreter._field_aad(table, column, row_key)).decode("utf-8")
    except InvalidTag:
        return "does not open for its own place (%s, %s, %s)" % (table, column, row_key)
    if plain is not None and got != plain:
        return "opens to %r, expected %r" % (got, plain)
    for aad in (MohioInterpreter._field_aad(table, column, str(row_key) + "9"), None):
        try:
            AESGCM(key).decrypt(nonce, ct, aad)
            return "also opens %s, so it is not bound to its row" % ("for another row" if aad else "with no place")
        except InvalidTag:
            pass
    return ""
