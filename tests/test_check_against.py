# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""check_against_stmt (`check VALUE against STORED`) never had a test before this file --
found while building the real-login recipe (2026-08-06). It is real, working code
(mohio_interpreter.py:_exec_CheckAgainstStmt), not a stub, but had zero regression coverage.

Adversarial: covers all three real hash-format branches the handler detects from the
stored value's shape (bcrypt `$2...`, pbkdf2 `pbkdf2_sha256$...`, sha256 64-hex), each
with a genuine match AND a genuine wrong-value mismatch -- a test that only tries the
happy path per algorithm would miss a handler that always returns True regardless of
the actual comparison.

Each case is its own standalone program with `check ... against ...` as the LAST
statement in the program. This is deliberate, not incidental: check_against_stmt has no
closer, and a real, currently-open compiler bug (logged in CLAUDE-CODE-BACKLOG.md, found
2026-08-06 while building this same recipe) silently drops any sibling statement that
follows a check block, and also mis-attaches an outer on.failure when check is nested
inside another block's on.success. Combining multiple cases into one program here would
launder that bug into a false pass; isolating each case is what makes this test honest
evidence, not just something that runs green.

Run: python tests/test_check_against.py
"""
import os, sys, hashlib, binascii, bcrypt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def run_case(candidate, stored):
    src = (
        f'check "{candidate}" against "{stored}"\n'
        f'    on.success\n'
        f'        show "MATCHED"\n'
        f'    on.failure\n'
        f'        show "MISMATCHED"\n'
    )
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter()
    it.run(prog)
    return list(it.shown)

def pbkdf2_hash(password, iters=1000):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iters)
    return f"pbkdf2_sha256${iters}${binascii.hexlify(salt).decode()}${binascii.hexlify(dk).decode()}"

PASSWORD = "correct-horse-battery-staple"
WRONG    = "guess"

# ── bcrypt ────────────────────────────────────────────────────────────────
bcrypt_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
check("bcrypt: correct password matches",
      run_case(PASSWORD, bcrypt_hash) == ['MATCHED'])
check("bcrypt: wrong password does not match",
      run_case(WRONG, bcrypt_hash) == ['MISMATCHED'])

# ── pbkdf2 ────────────────────────────────────────────────────────────────
pbkdf2_stored = pbkdf2_hash(PASSWORD)
check("pbkdf2: correct password matches",
      run_case(PASSWORD, pbkdf2_stored) == ['MATCHED'])
check("pbkdf2: wrong password does not match",
      run_case(WRONG, pbkdf2_stored) == ['MISMATCHED'])

# ── sha256 (64-hex checksum form) ────────────────────────────────────────
sha_stored = hashlib.sha256(PASSWORD.encode()).hexdigest()
check("sha256: correct value matches",
      run_case(PASSWORD, sha_stored) == ['MATCHED'])
check("sha256: wrong value does not match",
      run_case(WRONG, sha_stored) == ['MISMATCHED'])

# ── adversarial edge: a stored value that matches no known format must not crash,
#    and must not silently report a match it cannot back up (falls to plaintext-dev
#    compare, which is honest about being unverified rather than throwing) ─────────
check("unrecognized stored format: equal literal values still match (plaintext fallback)",
      run_case("plainvalue", "plainvalue") == ['MATCHED'])
check("unrecognized stored format: unequal literal values do not match",
      run_case("plainvalue", "somethingelse") == ['MISMATCHED'])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
