# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Check-time warning: `grant role` from a client-controlled value (auth sweep item 5, 2026-08-02).

`grant role` writes a VERIFIED server-side role. If the role value comes straight from the
request (`request.X`, or a field of the listener's own request shape), the caller picks their own
role -- the exact forgery `grant role` exists to close. The scan warns on that, and MUST NOT cry
wolf on the safe pattern: a role from a record the server looked up (`retrieve ... from db`).

Run: `python tests/test_grant_role_client_source_warn.py`.
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

def warns(src):
    errs, ws = run_scans(transform(_P.parse(src), src))
    return [w for w in ws if getattr(w, 'code', None) == 'grant_role_client_source']

REQUEST_ROOTED = ('shape Login\n    role as text\nshape: done\n'
                  'listen for\n    new sh.Login\n        grant role request.role\n'
                  '        give back 200 "ok"\n    new: done\nlisten: done\n')
SHAPE_VAR = ('shape Login\n    role as text\nshape: done\n'
             'listen for\n    new sh.Login\n        grant role login.role\n'
             '        give back 200 "ok"\n    new: done\nlisten: done\n')
LITERAL = ('shape Login\n    role as text\nshape: done\n'
           'listen for\n    new sh.Login\n        grant role "member"\n'
           '        give back 200 "ok"\n    new: done\nlisten: done\n')
DB_LOOKED_UP = ('shape Login\n    who as text\nshape: done\n'
                'connect db as postgres from env.DATABASE_URL\n'
                'listen for\n    new sh.Login\n        retrieve user from db.users\n'
                '            match id to 1\n        retrieve: done\n'
                '        grant role user.role\n        give back 200 "ok"\n    new: done\nlisten: done\n')

check("grant role request.role -> warns (client picks own role)", len(warns(REQUEST_ROOTED)) == 1,
      str(warns(REQUEST_ROOTED)))
check("grant role login.role (the request shape var) -> warns", len(warns(SHAPE_VAR)) == 1,
      str(warns(SHAPE_VAR)))
check("grant role \"member\" (literal) -> no warning", len(warns(LITERAL)) == 0)
check("grant role user.role from a db retrieve -> NO warning (safe pattern, no cry-wolf)",
      len(warns(DB_LOOKED_UP)) == 0, str(warns(DB_LOOKED_UP)))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
