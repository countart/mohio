# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Every table the compiler writes audit records to must satisfy `is_audit_table`.

WHY THIS IS A CONTRACT AND NOT A CONVENIENCE
The platform derives its append-only role grants from `is_audit_table`. A table that fails the
predicate is not covered by those grants, so audit records written there are ordinary rows a
tenant can update or delete -- the append-only guarantee silently does not apply to them, and
nothing anywhere reports a problem.

That had already happened. `compliance_audit` (the ai.decide record, the PRIMARY compliance log)
and `audit_incident_log` (which records an audit sink being too weak for the required grade) both
matched neither the static set nor either suffix family. They were real audit logs that the
platform's grants would have missed.

This test extracts the destinations from the source rather than restating them, so adding a new
audit writer with an unregistered name fails here instead of in production.
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_audit_grades import is_audit_table, STATIC_AUDIT_TABLES

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(_ROOT, 'mohio_interpreter.py'), encoding='utf-8').read()

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ── pull every literal audit destination out of the source ────────────────────────────
literals = set()
for pat in (r"_audit_event\(\s*'([a-z0-9_]+)'",
            r'_audit_event\(\s*"([a-z0-9_]+)"',
            r"_audit_chained_save\(\s*\w+\s*,\s*'([a-z0-9_]+)'",
            r'_audit_chained_save\(\s*\w+\s*,\s*"([a-z0-9_]+)"'):
    literals.update(re.findall(pat, SRC))

check("literal audit destinations were found in the source",
      len(literals) >= 3, f"found {sorted(literals)}")

for name in sorted(literals):
    check(f"'{name}' satisfies is_audit_table (platform grants cover it)",
          is_audit_table(name))

# ── the two that were missing are explicitly covered ──────────────────────────────────
check("compliance_audit is recognised (the primary ai.decide audit log)",
      is_audit_table('compliance_audit'))
check("audit_incident_log is recognised (records a too-weak audit sink)",
      is_audit_table('audit_incident_log'))

# ── the dynamic families still resolve ────────────────────────────────────────────────
check("per-agent '<name>_limits_log' is recognised",
      is_audit_table('payments_agent_limits_log'))
check("profile-custom '*_audit_log' is recognised",
      is_audit_table('some_profile_audit_log'))

# ── f-string destinations follow a recognised family ──────────────────────────────────
fstrings = set(re.findall(r'_audit_event\(\s*f"\{[^}]+\}([a-z0-9_]+)"', SRC))
for suffix in sorted(fstrings):
    check(f"dynamic destination ending '{suffix}' falls in a recognised family",
          is_audit_table('anything' + suffix), f"suffix={suffix!r}")

# ── an ordinary data table must NOT be mistaken for an audit table ────────────────────
for ordinary in ('members', 'orders', 'sessions', 'audit_notes', 'my_log'):
    check(f"ordinary table '{ordinary}' is not treated as an audit table",
          not is_audit_table(ordinary))


# ── the convention is ENFORCED at check time, not merely defined ──────────────────────
# A predicate nothing calls governs nothing. `ai.audit to decisions` was legal: it wrote audit
# records to an ordinary table outside the append-only grants, where a tenant can update or
# delete them and nothing reports it. Refusing at check time is the cheapest moment to say no --
# once a record is written it is already unprotected, and at the Certified tier it may already
# be sealed into storage that refuses deletion for years.
import subprocess, tempfile
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV = dict(os.environ, PYTHONPATH=_ROOT_DIR, DATABASE_URL=':memory:',
            MOHIO_ENCRYPTION_KEY='testkey')

_PROG = ('amt 100\n'
         'ai.decide d returns boolean\n'
         '    confidence above 0.85\n'
         '    weigh amt\n'
         '    ai.audit to %s\n'
         '    not confident\n'
         '        give back false\n'
         'ai.decide: done\n'
         'give back 200 "ok"\n')


def _check(dest):
    fd, path = tempfile.mkstemp(suffix='.mho')
    os.write(fd, (_PROG % dest).encode()); os.close(fd)
    r = subprocess.run([sys.executable, os.path.join(_ROOT_DIR, 'mio.py'), 'check', path],
                       env=_ENV, capture_output=True, text=True, timeout=90)
    os.unlink(path)
    return r.returncode, r.stdout + r.stderr


_code, _out = _check('decisions')
check("an ungoverned `ai.audit to` destination is refused at check time", _code != 0)
check("the refusal explains why and how to fix it",
      'append-only' in _out and '_audit_log' in _out)

_code2, _out2 = _check('decisions_audit_log')
check("a governed `*_audit_log` destination passes", _code2 == 0, _out2[-160:])

_code3, _out3 = _check('phi_audit_log')
check("a standard log name passes", _code3 == 0, _out3[-160:])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
