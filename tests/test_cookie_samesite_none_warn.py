# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Check-time warning: `same site "none"` without `secure` (2026-08-02).

SameSite=None is a hard browser rule -- a SameSite=None cookie with no Secure flag is silently
rejected, so the cookie is never set. This warns at check time. `same site "none"` WITH `secure`
is fine; every other same-site value is unaffected.

Run: `python tests/test_cookie_samesite_none_warn.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_reachability import run_scans

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def warns(body_lines):
    src = ('shape Cmd\n    command as text\nshape: done\n'
           'listen for\n    new sh.Cmd\n'
           '        miocookie.set "sid"\n            value "x"\n'
           + body_lines +
           '        miocookie.set: done\n        give back 200 "ok"\n    new: done\nlisten: done\n')
    errs, ws = run_scans(transform(_P.parse(src), src))
    return [w for w in ws if getattr(w, 'code', None) == 'cookie_samesite_none_insecure']

check('same site "none" alone -> warns',
      len(warns('            same site "none"\n')) == 1, str(warns('            same site "none"\n')))
check('same site "none" WITH secure -> no warning',
      len(warns('            same site "none"\n            secure\n')) == 0)
check('same site "strict" -> no warning', len(warns('            same site "strict"\n')) == 0)
check('same site "lax" -> no warning', len(warns('            same site "lax"\n')) == 0)
check('no same site clause at all -> no warning', len(warns('            secure\n')) == 0)
# Case-insensitive: NONE and None also warn (browser rule is on the value, not its casing).
check('same site "NONE" (any casing) alone -> warns',
      len(warns('            same site "NONE"\n')) == 1)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
