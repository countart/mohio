# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Ed25519 authenticity for certified sector profiles.

RON'S RULINGS, encoded here:
  - The FILE FORMAT carries the tier. A `.sector` is a CERTIFIED profile; a `.mho` sector profile is
    community and free. Tier is decided by what a profile IS (a `.sector` whose signature verifies),
    never by what it says.
  - A `.sector` is verified by SIGNATURE: Mohio signs the profile body with a private key; the runtime
    verifies against Mohio's PUBLIC key. A hash alone proves nothing, so this signs, it does not merely
    hash.
  - FAIL CLOSED. A `.sector` that carries no signature, or whose signature does not verify against a
    trusted key, is refused. Never a silent downgrade to a same-named `.mho`.
  - Verify against a SET of trusted public keys, configured PER DEPLOYMENT, never one hardcoded key.
    Today the set holds only Mohio's key, but nothing here assumes exactly one, so a customer's own
    Mohio-endorsed key can be added to that customer's deployment later without touching the verifier.

NOT built here (platform work, per the batch): backend entitlement (is this customer licensed for
certified profiles), the secure-area fetch, a certificate chain / delegation, and the production key.

FILE FORMAT of a signed `.sector`, byte-exact so the signature covers exactly what is stored:

    // MOHIO-SECTOR-SIGNATURE v1 ed25519
    // sig: <base64 of the 64-byte Ed25519 signature over the BODY bytes>
    <profile body -- the ordinary .sector text>

The signature is over the BODY bytes (everything after the two header lines), read and verified as
raw bytes, so newline or BOM differences never change the verdict silently.
"""
import base64
import glob
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.exceptions import InvalidSignature

SIG_MARKER = b"// MOHIO-SECTOR-SIGNATURE v1 ed25519"
SIG_LINE_PREFIX = b"// sig: "
TRUSTED_KEYS_ENV = "MOHIO_SECTOR_TRUSTED_KEYS"
CERTIFIED_LOCATION_ENV = "MOHIO_CERTIFIED_SECTORS_DIR"


class SectorVerificationError(Exception):
    """A `.sector` profile failed authenticity verification. Fail closed: the program is refused."""


def split_signed(data: bytes):
    """(signature_b64_bytes or None, body_bytes). None signature means the file is unsigned."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.startswith(SIG_MARKER):
        return None, data
    nl1 = data.find(b"\n")
    if nl1 < 0:
        return None, data
    rest = data[nl1 + 1:]
    if not rest.startswith(SIG_LINE_PREFIX):
        return None, data
    nl2 = rest.find(b"\n")
    if nl2 < 0:
        return None, data
    sig_b64 = rest[len(SIG_LINE_PREFIX):nl2].strip()
    body = rest[nl2 + 1:]
    return sig_b64, body


def sign_body(body: bytes, private_key: Ed25519PrivateKey) -> bytes:
    """The base64 signature over the body bytes."""
    return base64.b64encode(private_key.sign(body))


def make_signed_file(body: bytes, private_key: Ed25519PrivateKey) -> bytes:
    sig_b64 = sign_body(body, private_key)
    return SIG_MARKER + b"\n" + SIG_LINE_PREFIX + sig_b64 + b"\n" + body


def verify(body: bytes, sig_b64: bytes, public_keys) -> bool:
    """True if the signature verifies against ANY key in the trusted set. The set is a list on
    purpose, so more than one trusted signer is supported without changing this function."""
    try:
        sig = base64.b64decode(sig_b64)
    except Exception:
        return False
    for pk in public_keys:
        try:
            pk.verify(sig, body)
            return True
        except InvalidSignature:
            continue          # this key did not sign it -- try the next trusted key
        except Exception:
            # a malformed key object: skip it. FAIL-CLOSED SAFE -- if no key verifies we return False
            # below, so a swallowed error here can only refuse, never accept a forgery.
            continue
    return False


# ── key material (hex-encoded raw 32-byte Ed25519 keys) ──────────────────────
def load_private_key(path: str) -> Ed25519PrivateKey:
    raw = open(path, "rb").read().strip()
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw.decode("ascii")))


def private_key_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex()


def public_key_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def load_public_key_hex(hex_text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_text.strip()))


def trusted_public_keys():
    """The per-deployment trusted set, from MOHIO_SECTOR_TRUSTED_KEYS: an OS-pathsep-separated list
    of entries, each a public-key file (hex) or a directory of `*.pub` files. Every key found is
    trusted, so adding a signer is adding a path -- no verifier change, no single hardcoded key. An
    unset or empty variable yields an EMPTY set, so every `.sector` then fails closed (nothing is
    trusted until a deployment says what to trust)."""
    keys = []
    for entry in os.environ.get(TRUSTED_KEYS_ENV, "").split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        paths = (sorted(glob.glob(os.path.join(entry, "*.pub")))
                 if os.path.isdir(entry) else [entry] if os.path.isfile(entry) else [])
        for p in paths:
            try:
                keys.append(load_public_key_hex(open(p, encoding="utf-8").read()))
            except Exception:
                # a bad or unreadable key file is skipped. FAIL-CLOSED SAFE -- if that empties the
                # trusted set, every `.sector` is refused; a skipped key can never open the gate.
                continue
    return keys
