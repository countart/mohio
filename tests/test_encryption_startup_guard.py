# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""OQ-025: the encryption startup guard -- refuse to boot an app that must encrypt but cannot.

The backends (`cryptography`, `bcrypt`) are imported LAZILY at the point of use, so before this
guard a stripped/broken lib first surfaced mid-write -- after the app booted, looked healthy, and
accepted data. For a compliance product "boots but can't seal" is the worst failure mode. The
guard fires at startup (`run_declarations`), and ONLY when encryption is contractually required
(an encrypted / [phi]/[pii]/[pci] / sec.encrypt field, or a bcrypt password hash). An app that
needs neither is never forced to have the backend.

Backends are present in this environment, so unavailability is SIMULATED by blocking the import
via sys.modules -- exercising the real guard path. Run: `python tests/test_encryption_startup_guard.py`.
"""
import os, sys, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)
def prog(src): return transform(_P.parse(src), src)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def boots(src, blocked=None):
    """Return None if run_declarations succeeds, else the raised message."""
    ctx = unittest.mock.patch.dict(sys.modules, {m: None for m in (blocked or [])})
    with ctx:
        try:
            MohioInterpreter().run_declarations(prog(src))
            return None
        except MohioRuntimeError as e:
            return str(e)

ENC   = 'shape Intake [phi]\n    ssn as text\nshape: done\n'          # [phi] -> ssn encrypted
NOENC = 'shape Note\n    body as text\nshape: done\n'                 # nothing encrypted
BCRYPT   = 'hold pw "secret"\nhash pw as hashed using bcrypt\n'       # a bcrypt password hash
NOBCRYPT = 'hold pw "secret"\nshow pw\n'                              # no hashing

CRYPTO = ['cryptography', 'cryptography.hazmat.primitives.ciphers.aead']
BCRYPT_MOD = ['bcrypt']

# ── cryptography (encryption at rest) ───────────────────────────────────────────────────
check("encrypted app + backend present -> BOOTS", boots(ENC) is None)
_m = boots(ENC, blocked=CRYPTO)
check("encrypted app + backend UNAVAILABLE -> refuses to boot", _m is not None, "should raise")
check("...the refusal names cryptography AND the field",
      bool(_m) and 'cryptography' in _m and 'ssn' in _m, _m or '')
check("no-encryption app + backend unavailable -> BOOTS (guard conditional)",
      boots(NOENC, blocked=CRYPTO) is None,
      "an app with no encrypted fields must not be forced to have cryptography")

# ── bcrypt (password hashing) ───────────────────────────────────────────────────────────
check("bcrypt-hashing app + bcrypt present -> BOOTS", boots(BCRYPT) is None)
_b = boots(BCRYPT, blocked=BCRYPT_MOD)
check("bcrypt-hashing app + bcrypt UNAVAILABLE -> refuses to boot", _b is not None, "should raise")
check("...the refusal names bcrypt", bool(_b) and 'bcrypt' in _b, _b or '')
check("no-hashing app + bcrypt unavailable -> BOOTS (guard conditional)",
      boots(NOBCRYPT, blocked=BCRYPT_MOD) is None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
