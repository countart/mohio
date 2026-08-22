# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""require role ignores the client `_roles` payload entirely (auth rebuild Item 1, 2026-08-02).

History: the caller's roles used to arrive in the request `_roles` field, which the client fully
controls. First a forged `_roles:["admin"]` was trusted outright; the S8.4 fix made `require role`
REFUSE unverified client roles unless MOHIO_TRUST_PROXY_ROLES=1, which then trusted them wholesale.
Item 1 removes the client-roles path completely: `_roles` is never read, and the env var's
wholesale-trust bypass is gone. Roles are established server-side by `grant role` (see
test_grant_role_server_derived.py for the end-to-end proof); this file guards the STATELESS
`run()` path -- that no client payload, with or without the env var, grants any authority.

Run: `python tests/test_require_role_unverified.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter
from mohio_transformer_ast import transform as ast_transform

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SRC = ('shape AdminAction\n    action as text required\nshape: done\n'
       'listen for\n    new sh.AdminAction\n        require role "admin"\n'
       '        give back 200 "ADMIN OK"\n    new: done\nlisten: done\n')

def hit(roles):
    req = {'method': 'POST', 'path': '/', 'action': 'wipe', '_roles': roles}
    return MohioInterpreter().run(ast_transform(P.parse(SRC), SRC), request=req)

# DEFAULT: no trust declared -> a forged client payload grants nothing.
os.environ.pop('MOHIO_TRUST_PROXY_ROLES', None)
r = hit(['admin'])
_b = str(r.get('body', '')).lower()
check("forged _roles=['admin'] grants nothing -> 403 'Role required' (payload ignored)",
      r.get('status') == 403 and 'role required' in _b, str(r))
check("the refusal does NOT execute the protected action",
      'ADMIN OK' not in str(r.get('body', '')), str(r))
r = hit([])
check("no roles -> 403 'Role required'", r.get('status') == 403
      and 'role required' in str(r.get('body', '')).lower(), str(r))
# The refusal is self-diagnosing: it names the fix (grant role), not just the missing role, so a
# 403 after the auth rebuild is not a mystery outage (the exact shape Zork hit in production).
check("the refusal points at the fix (mentions `grant role`)",
      'grant role' in str(r.get('body', '')).lower(), str(r))

# The wholesale-trust bypass is GONE: MOHIO_TRUST_PROXY_ROLES=1 no longer trusts the payload.
# (The legitimate reverse-proxy case returns later behind an explicit trusted-header declaration,
#  brief Requirement 1 -- not this env var.)
os.environ['MOHIO_TRUST_PROXY_ROLES'] = '1'
r = hit(['admin'])
check("MOHIO_TRUST_PROXY_ROLES=1 + forged _roles=['admin'] -> STILL 403 (bypass removed)",
      r.get('status') == 403, str(r))
r = hit(['user'])
check("MOHIO_TRUST_PROXY_ROLES=1 + wrong role -> 403", r.get('status') == 403, str(r))
os.environ.pop('MOHIO_TRUST_PROXY_ROLES', None)

# T1-AUDIT-COVERAGE-GAPS Part B (2026-08-17): a require-role denial is a security-relevant
# event -- only grants were being audited, denials were silent. Verify the denial itself
# writes to security_audit_log, using a fresh interpreter so the log is empty beforehand.
it = MohioInterpreter()
r = it.run(ast_transform(P.parse(SRC), SRC), request={'method': 'POST', 'path': '/', 'action': 'wipe'})
_log = it._audit_logs.get('security_audit_log', [])
check("a require-role denial (no roles) writes a security_audit_log entry",
      any(e.get('event') == 'access_denied' and e.get('reason') == 'role_not_present'
          for e in _log), _log)
check("the denial entry names the required role",
      any('admin' in (e.get('required_roles') or []) for e in _log), _log)

# Test-strength check (content-safety review, 2026-08-19): the denial write must go through
# _audit_event, not a hand-rolled call straight to _audit_chained_save (the M2/M3 bypass
# pattern the architectural rule forbids repeating). Spy on the real bound method so a
# regression back to that pattern is caught directly, not inferred from row shape.
_calls = []
_orig_audit_event = MohioInterpreter._audit_event
def _spy_audit_event(self, log_name, entry, ctx):
    _calls.append((log_name, entry.get('event'), entry.get('reason')))
    return _orig_audit_event(self, log_name, entry, ctx)
MohioInterpreter._audit_event = _spy_audit_event
try:
    it2 = MohioInterpreter()
    it2.run(ast_transform(P.parse(SRC), SRC), request={'method': 'POST', 'path': '/', 'action': 'wipe'})
finally:
    MohioInterpreter._audit_event = _orig_audit_event
check("the require-role denial audit goes through _audit_event (not a bypass)",
      ('security_audit_log', 'access_denied', 'role_not_present') in _calls, _calls)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
