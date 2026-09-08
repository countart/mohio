# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""What the system PREVENTED or REVERSED reaches the trail, not only what it allowed.

AUDIT COMPLETENESS RUNS IN TWO DIRECTIONS. One is "does the trail claim something that did not
happen", which the weigh work covered. This is the other: "does something happen that the trail
does not say". Two controls fired and left nothing behind, so afterwards a control that WORKED
was indistinguishable from one that was never tested, and what a control prevented IS the
content of the compliance claim.

MEASURED BEFORE, reading the audit after a real request rather than the response:

    purpose violation fires   500 with the GDPR Art. 5(1)(b) message   audit: no violation row
    permitted purpose use     200                                      audit: PURPOSE_USE
    saga fails, compensates   500, step trace on stderr                audit: nothing at all
    role denial               403                                      audit: access_denied

So the trail recorded every ALLOWED use and none of the stops, and a rolled-back transaction was
indistinguishable from unrelated writes. Role denial was already right, which is what made the
fix small: point the two silent paths at the mechanism it already uses.

WHERE EACH ROW GOES, and the split is deliberate. A purpose refusal joins `access_denied` in
`security_audit_log`, because a control refusing is the same kind of event whether the control
is a role or a purpose. A saga compensation goes to `operation_audit_log`, because an
operational reversal is not an access denial and belongs beside the operational records.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_prevention_audited.py
"""
import os
import sys

sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform  # noqa: E402
from mohio_interpreter import MohioInterpreter  # noqa: E402
from mohio_server import MohioServer, create_app  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond:
        if detail:
            print("          " + str(detail)[:400])
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


def serve(src):
    """(status, body, {log_name: [row, ...]}) after one real request."""
    prog = transform(P.parse(src), src)
    it = MohioInterpreter()
    client = TestClient(create_app(MohioServer(prog, it)), raise_server_exceptions=False)
    r = client.post("/p", json={})
    logs = {}
    for name, entries in (getattr(it, '_audit_logs', {}) or {}).items():
        rows = entries if isinstance(entries, list) else getattr(entries, 'entries', [])
        logs[name] = [e for e in rows if isinstance(e, dict)]
    return r.status_code, r.text, logs


def rows_of(logs, log_name, event):
    return [e for e in logs.get(log_name, []) if e.get('event') == event]


SHAPE = ('shape Patient\n'
         '    email as text [pii]\n        purpose "billing"\n'
         '    name as text\n'
         'shape: done\n'
         'connect db as sqlite from env.DATABASE_URL\n')
SEED = ('        save to db.patients\n            email "a@b.com"\n            name "Ada"\n'
        '        save: done\n'
        '        retrieve p from db.patients\n            match name to "Ada"\n'
        '            on.failure\n                give back [404] "none"\n'
        '        retrieve: done\n')


def purpose_app(scope, use='give back 200 p.email'):
    return (SHAPE + 'shape Q\nshape: done\nlisten for\n    new sh.Q at /p\n' + SEED +
            f'        purpose "{scope}"\n            {use}\n        purpose: done\n'
            '    new: done\nlisten: done\n')


# ── 1. the purpose-limitation refusal ────────────────────────────────────────────────────────
print("\n== a purpose violation is recorded, not only refused ==")
status, body, logs = serve(purpose_app("marketing"))
check("the control still refuses", status == 500, (status, body[:120]))
check("...citing GDPR Art. 5(1)(b) as it always did", "5(1)(b)" in body, body[:160])
viol = rows_of(logs, 'security_audit_log', 'purpose_violation')
check("...and NOW writes a security_audit_log row", len(viol) == 1, logs.get('security_audit_log'))
# NO `if viol:` GUARD. It was written that way first, and the mutation proof showed why that is
# wrong: with the row missing, only "writes a row" failed and every content check silently did
# not run, so a fix that wrote an EMPTY row would have passed almost everything. An absent row
# now reddens each thing the row was supposed to say.
row = viol[0] if viol else {}
if True:
    check("...naming the field that was stopped", row.get('field') == 'email', row)
    check("...the purpose that was ATTEMPTED", row.get('attempted_purpose') == 'marketing', row)
    check("...the purposes that ARE allowed", row.get('allowed_purposes') == ['billing'], row)
    check("...a machine-readable reason", row.get('reason') == 'purpose_not_permitted', row)
    check("...and which kind of use it was", row.get('use') == 'direct', row)
    print("  -- and it is chained like the rows beside it --")
    check("the row carries a timestamp", bool(row.get('ts')), row)
    check("...and an entry hash", bool(row.get('entry_hash')), sorted(row))
    check("...and the previous hash, which is what makes it a chain",
          'prev_hash' in row, sorted(row))

print("\n-- a PERMITTED use writes no violation row --")
status, body, logs = serve(purpose_app("billing"))
check("the permitted use succeeds", status == 200, (status, body[:120]))
check("...and no violation is recorded",
      rows_of(logs, 'security_audit_log', 'purpose_violation') == [], logs)
check("...while the allowed use is still recorded as before",
      any(e.get('event') == 'PURPOSE_USE' for e in logs.get('data_audit_log', [])), logs)

print("\n-- the DERIVED sibling refuses and records too --")
# A value COPIED from a [pii] field carries the field's purposes. Auditing the direct site and
# not this one would make the trail depend on how the program happened to be written.
status, body, logs = serve(purpose_app("marketing", 'x p.email\n            give back 200 x'))
check("a copied value is still refused", status == 500, (status, body[:120]))
dv = rows_of(logs, 'security_audit_log', 'purpose_violation')
check("...and records a violation row", len(dv) >= 1, logs.get('security_audit_log'))
check("...marked as a derived use", dv and dv[0].get('use') == 'derived', dv)

# ── 2. the saga compensation ─────────────────────────────────────────────────────────────────
SAGA_HEAD = ('shape Q\nshape: done\nconnect db as sqlite from env.DATABASE_URL\n'
             'listen for\n    new sh.Q at /p\n'
             '        saga placeOrder\n'
             '            step reserve\n'
             '                save to db.reservations\n                    item "widget"\n'
             '                save: done\n'
             '                compensate\n'
             '                    remove.all from db.reservations\n'
             '            step: done\n')
SAGA_TAIL = ('            on.failure\n                give back [500] "could not place"\n'
             '        saga: done\n'
             '        give back 201 "placed"\n    new: done\nlisten: done\n')
FAILING_STEP = ('            step charge\n'
                '                save to db.charges\n'
                '                    amount undefined_thing.nope\n'
                '                save: done\n'
                '            step: done\n')

print("\n== a compensated saga is recorded, not only printed to stderr ==")
status, body, logs = serve(SAGA_HEAD + FAILING_STEP + SAGA_TAIL)
check("the saga still fails loudly", status == 500, (status, body[:120]))
comp = rows_of(logs, 'operation_audit_log', 'saga_compensated')
check("...and writes an operation_audit_log row", len(comp) == 1, logs.get('operation_audit_log'))
row = comp[0] if comp else {}
if True:
    check("...naming the saga", row.get('saga') == 'placeOrder', row)
    check("...naming the step that failed", row.get('failed_step') == 'charge', row)
    check("...counting the steps compensated", row.get('steps_compensated') == 1, row)
    check("...with a machine-readable reason", row.get('reason') == 'step_failed', row)
    check("...and the terminal status", row.get('status') == 'COMPENSATED', row)
    check("the row is chained like the others",
          bool(row.get('ts')) and bool(row.get('entry_hash')) and 'prev_hash' in row,
          sorted(row))

print("\n-- a saga that COMMITS writes no compensation row --")
status, body, logs = serve(SAGA_HEAD + SAGA_TAIL)
check("the successful saga returns its own response", status == 201, (status, body[:120]))
check("...and records no compensation",
      rows_of(logs, 'operation_audit_log', 'saga_compensated') == [],
      logs.get('operation_audit_log'))

# ── the control that was already right, still right ──────────────────────────────────────────
print("\n== role denial, which was already audited, is untouched ==")
status, body, logs = serve('shape Q\nshape: done\nlisten for\n    new sh.Q at /p\n'
                           '        require role "admin"\n'
                           '        give back 200 "ok"\n'
                           '    new: done\nlisten: done\n')
check("role denial still refuses", status == 403, (status, body[:120]))
denied = rows_of(logs, 'security_audit_log', 'access_denied')
check("...and still records access_denied with its reason",
      len(denied) == 1 and denied[0].get('reason') == 'role_not_present', denied)
check("...in the same log the purpose violation now uses",
      'security_audit_log' in logs, sorted(logs))

if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
