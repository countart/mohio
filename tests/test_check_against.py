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

# ── sha512 (128-hex) ──────────────────────────────────────────────────────
# Added 2026-09-01. `hash ... using sha512` was always legal and always produced a hash this
# statement could not verify -- only the 64-hex sha256 length was recognised, so a sha512
# digest fell through to the old plaintext compare and answered MISMATCHED. A supported
# algorithm reporting "wrong password" is the worst way to say "unsupported algorithm".
sha512_stored = hashlib.sha512(PASSWORD.encode()).hexdigest()
check("sha512: correct value matches (was: MISMATCHED, always)",
      run_case(PASSWORD, sha512_stored) == ['MATCHED'])
check("sha512: wrong value does not match",
      run_case(WRONG, sha512_stored) == ['MISMATCHED'])

# ── a stored value that is not a hash at all ──────────────────────────────
# CORRECTED 2026-09-01. This previously asserted that two equal literals MATCH, and called the
# behaviour "honest about being unverified rather than throwing". It was the opposite of
# honest: the program printed MATCHED and nothing anywhere said the value had never been
# hashed. Run through the CLI, an app whose stored passwords were still plaintext printed
# LOGGED IN, while `mio check` -- which announces "full compliance and security analysis" --
# reported no errors. A verifier that cannot verify must say so, not return True.
def refusal(candidate, stored):
    try:
        run_case(candidate, stored)
        return None
    except Exception as e:
        return str(e)

_msg = refusal("plainvalue", "plainvalue")
check("a stored value that is not a hash is REFUSED (was: MATCHED, silently)",
      _msg is not None and "not a hash this can verify" in _msg, _msg)
check("...and the message names the unhashed-password case as the likely bug",
      _msg is not None and "never hashed" in _msg, _msg)
check("...and points to `check` / `when` for comparing two ordinary values",
      _msg is not None and "check` / `when" in _msg, _msg)
check("unequal non-hash values are refused too, not reported as a mismatch",
      refusal("plainvalue", "somethingelse") is not None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
