# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Sector RUNTIME enforcement is real: a profile that FORBIDS or REVIEW-gates an operation actually
stops it at runtime (family-enumeration sweep, F7, 2026-07-31).

The sector-profile claim is "declare it and it activates." A profile can declare three constraint
types (SectorProfile): `never_store_fields` and `confidence_floors` (enforced at COMPILE time --
covered by test_sector_enforcement) and `operation_rules` (enforced at RUNTIME on a connector call).
The runtime path had NO test: mutating the enforcement to drop `forbidden` -- a forbidden operation
silently runs -- survived all 27 compliance/security tests. That makes the flagship claim unverified.

This test DERIVES the runtime-enforced verdict family from the interpreter code
(`_exec_MioconnectCall`'s `if verdict in (...)`) and, for each enforced verdict, runs a REAL program
that calls a governed connector operation and asserts it is refused and NAMED. A new enforced
verdict added to the code with no case fails the build. The `allowed` verdict is asserted to pass
governance (it proceeds to the wire, mocked). The enforcement fires BEFORE credentials and the wire,
so a forbidden call never reaches the network.

Run as a script: `python tests/test_sector_runtime_enforcement.py` (exit 0 = pass).
"""
import os, sys, re, inspect, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter
from mohio_transformer_ast import transform as ast_transform
import mohio_sector_loader as SL

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# ── a fixture profile that declares ALL THREE constraint types ─────────────────────────────
FIXTURE = '''sector: testfixture
    field types
        ssn is [phi, pii]
            never store
            label "Social security number"
    field: done

    ai.decide rules for sector "testfixture"
        minimum confidence 0.85 for fraud_screen
    ai.decide: done

operation rules for sector "testfixture"
    any operation "Stripe.refund"
        forbidden
        reason "refunds are forbidden under the test profile"
    any operation "Stripe.charge"
        requires human review
        reason "charges need human review under the test profile"
operation: done
'''
PROFILE = SL._parse_sector_profile_text(FIXTURE, "<fixture>")

# enumerate the constraint types SectorProfile can carry, and confirm the fixture exercises each.
check("fixture declares a never-store constraint", bool(PROFILE.never_store_fields), str(PROFILE.never_store_fields))
check("fixture declares a confidence-floor constraint", bool(PROFILE.confidence_floors), str(PROFILE.confidence_floors))
check("fixture declares operation-rule constraints", bool(PROFILE.operation_rules), str(PROFILE.operation_rules))

# inject the fixture on the real load path (imported inside _exec_SectorDecl).
_orig = SL.get_sector_profile
SL.get_sector_profile = lambda name, *a, **k: PROFILE if name == "testfixture" else _orig(name, *a, **k)

# ── derive the RUNTIME-enforced verdict family from the code ────────────────────────────────
src = inspect.getsource(MohioInterpreter._exec_MioconnectCall)
m = re.search(r"verdict in \(([^)]*)\)", src)
ENFORCED = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()
check("derived the enforced-verdict family from _exec_MioconnectCall", ENFORCED >= {"forbidden"}, str(ENFORCED))
print(f"    enforcement acts on verdicts: {sorted(ENFORCED)}")

# REQUIRED is the invariant, NOT derived: a sector profile MUST be able to forbid and to
# review-gate an operation, and that enforcement must never regress. Asserting REQUIRED <= ENFORCED
# (derived) catches enforcement being REMOVED -- if the code drops 'forbidden' from its tuple,
# ENFORCED loses it and this fails, NAMING the missing verdict. (A derived-only loop would go
# circular: removing the verdict would also remove its own test.)
ERROR_NAME = {"forbidden": "sector_forbids_operation", "review": "sector_requires_review"}
REQUIRED = {"forbidden", "review"}
missing_enforcement = REQUIRED - ENFORCED
check("the code still ENFORCES every required verdict (forbidden, review) -- names any dropped",
      not missing_enforcement, f"these verdicts are no longer enforced at runtime: {sorted(missing_enforcement)}")
uncovered = ENFORCED - set(ERROR_NAME)
check("every enforced verdict has a known refusal error name (add one for a new verdict)",
      not uncovered, f"enforced verdicts with no expected error name: {sorted(uncovered)}")

OPS = ["refund", "charge", "ping"]     # refund->forbidden, charge->review, ping->allowed(no rule)
def verdict_of(op): return PROFILE.get_operation_verdict("Stripe", op)[0]

def run_call(op):
    prog = ('sector: testfixture\n'
            'mioconnect Stripe\n'
            '    address "https://api.stripe.com/v1"\n'
            + ''.join(f'    operation {o}\n        path "/{o}"\n    operation: done\n' for o in OPS) +
            'mioconnect: done\n'
            'payload "x"\n'
            f'Stripe.{op} with payload as result\n')
    interp = MohioInterpreter()
    # mock the wire so an ALLOWED op that passes governance does not touch the network.
    with unittest.mock.patch('urllib.request.urlopen', _mock_urlopen):
        return interp.run(ast_transform(P.parse(prog), prog))

def _mock_urlopen(req, **kw):
    r = unittest.mock.MagicMock()
    r.status = 200; r.getcode.return_value = 200
    r.read.return_value = b'{"ok":true}'
    r.__enter__ = lambda s: r; r.__exit__ = lambda s, *a: False
    r.headers = {}
    return r

# ── each REQUIRED verdict: a real governed call is REFUSED and NAMED (fixed, not derived) ───
for verdict in sorted(REQUIRED):
    ops_for = [o for o in OPS if verdict_of(o) == verdict]
    check(f"the fixture has an operation producing verdict '{verdict}'", bool(ops_for),
          f"no fixture operation yields '{verdict}' -- extend the fixture")
    if not ops_for:
        continue
    res = run_call(ops_for[0])
    body = str(res.get('body', '') if isinstance(res, dict) else res)
    check(f"a '{verdict}' operation ({ops_for[0]}) is REFUSED at runtime, status 500",
          isinstance(res, dict) and res.get('status') == 500, str(res)[:160])
    check(f"the refusal names '{ERROR_NAME[verdict]}'", ERROR_NAME[verdict] in body, body[:160])

# ── an ALLOWED operation passes governance (is NOT refused with a sector error) ─────────────
allowed_ops = [o for o in OPS if verdict_of(o) == "allowed"]
check("the fixture has an allowed (ungoverned) operation", bool(allowed_ops), str(OPS))
if allowed_ops:
    res = run_call(allowed_ops[0])
    body = str(res.get('body', '') if isinstance(res, dict) else res).lower()
    check("an allowed operation is NOT refused by the sector (governance lets it through)",
          'sector_forbids_operation' not in body and 'sector_requires_review' not in body,
          str(res)[:160])

SL.get_sector_profile = _orig
print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
