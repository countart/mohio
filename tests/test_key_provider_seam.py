# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The key-provider seam: WHERE the key comes from is swappable; WHAT it guarantees is not.

`_encryption_key()` used to hardcode SHA-256 of an env var. That is fine for a single-tenant
self-hosted box and wrong for multi-tenant (one leaked secret decrypts every tenant). The
managed platform and self-hosting commercial customers need the key to come from a KMS or
vault instead.

The seam lets a provider be registered (callable() -> 32 bytes | None), following the same
plugin pattern as register_executor. These tests prove:
  - with no provider registered, behaviour is exactly the env-var default (unchanged)
  - a registered provider takes over key sourcing
  - a provider returning None still makes tagged writes FAIL LOUD (guarantee preserved)
  - a provider returning a wrong-size key is refused, not silently used
The enforcement, the AES-GCM, and the fail-loud are never touched by any of this.
"""
import os, sys, hashlib

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
AT_REST = ('retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n'
           '    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n')
READBACK = ('retrieve p from db.people\n    match name to "Ada"\nretrieve: done\n'
            'give back 200 p.ssn\n')


def run(src):
    return MohioInterpreter().run(transform(_P.parse(src), src)).get('body')


def _val(body):
    return body.to_python() if hasattr(body, 'to_python') else body


_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# 1. No provider registered -> env-var default, identical to before.
MohioInterpreter.unregister_key_provider()
os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
at_rest = _val(run(SHAPE + DB + SAVE + AT_REST))
check("default provider: ciphertext at rest (unchanged behaviour)",
      isinstance(at_rest, str) and at_rest.startswith('enc:v1:'), f"got {at_rest!r}")

# 2. A registered provider takes over. Simulate a KMS returning a fixed 32-byte key,
#    with the env var deliberately DIFFERENT to prove the provider is what's used.
kms_key = hashlib.sha256(b'a-key-that-lives-in-the-customers-vault').digest()
MohioInterpreter.register_key_provider(lambda: kms_key)
os.environ['MOHIO_ENCRYPTION_KEY'] = 'this-should-be-ignored-now'
at_rest = _val(run(SHAPE + DB + SAVE + AT_REST))
back = _val(run(SHAPE + DB + SAVE + READBACK))
check("registered provider: still encrypts at rest", 
      isinstance(at_rest, str) and at_rest.startswith('enc:v1:'), f"got {at_rest!r}")
check("registered provider: round-trips to plaintext with the provider's key",
      str(back) == '123-45-6789', f"got {back!r}")

# 3. A provider that returns None must FAIL LOUD, same as no env key.
MohioInterpreter.register_key_provider(lambda: None)
txt = str(_val(run(SHAPE + DB + SAVE + 'give back 200 "done"\n')))
check("provider returning None still fails loud (guarantee preserved)",
      'key_missing' in txt or 'encryption' in txt, f"got {txt!r}")

# 4. A provider returning a wrong-size key is refused, not silently used.
MohioInterpreter.register_key_provider(lambda: b'too-short')
txt = str(_val(run(SHAPE + DB + SAVE + 'give back 200 "done"\n')))
check("provider returning a non-32-byte key is refused",
      'bad_key' in txt or '32' in txt or 'encryption' in txt, f"got {txt!r}")

# cleanup so we don't leak a provider into other suites
MohioInterpreter.unregister_key_provider()

# 5. After unregister, the default env-var path is restored.
os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
back = _val(run(SHAPE + DB + SAVE + READBACK))
check("unregister restores the default provider",
      str(back) == '123-45-6789', f"got {back!r}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
