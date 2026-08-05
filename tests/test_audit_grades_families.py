# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Structural coverage for the audit-grade dispatch families (family-enumeration sweep, 2026-07-31).

The family-enumeration pattern that closed the tombstone verifier and classify_sink is applied
here to the two remaining dict/set families in mohio_audit_grades.py. Mutation probing found the
same disease: only 4 of ~19 frameworks in FRAMEWORK_AUDIT_GRADE were tested (hand-listed), so
silently mapping `finra` or `hitech` to "none" -- a compliance framework losing its mandated audit
requirement -- survived the whole suite.

Each family is DERIVED from the code (the dict / the frozenset) and checked against an invariant
that does NOT move with the value, so a mutation cannot satisfy both sides. A new member added to
the dict is covered automatically or fails the build.

  FRAMEWORK_AUDIT_GRADE : every framework requires a VALID, AUDIT-BEARING grade (>= durable, never
      "none"); the resolver round-trips each key; highest-wins holds across every pair; an unknown
      framework is surfaced. Catches downgrade-to-none and any resolver break.
  UNRATIFIED_MAPPINGS   : every unratified name is a real framework in the table AND is flagged by
      required_grade(return_unratified=True). Catches a flagging-mechanism break.
  GRADES ladder         : _rank strictly increasing; satisfies is exactly the >= relation; stronger
      is max. (Already caught by test_audit_fail_loud; locked here as part of the family.)

LIMIT (stated, not hidden): a downgrade BETWEEN two audit-bearing grades (e.g. sec17a4 worm ->
durable) is NOT caught -- there is no code-independent oracle for a framework's exact grade, and
pinning exact values would be the hand-list this sweep exists to avoid. The invariant catches the
compliance-critical class (a framework silently losing its audit requirement) and every new member.

Run as a script: `python tests/test_audit_grades_families.py` (exit 0 = pass).
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ.pop('MOHIO_FRAMEWORK_GRADES', None)   # no deployment overrides during the invariant check

from mohio_audit_grades import (GRADES, _rank, satisfies, stronger, required_grade,
                                FRAMEWORK_AUDIT_GRADE, UNRATIFIED_MAPPINGS)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

DURABLE = _rank("durable")

# ── F3: FRAMEWORK_AUDIT_GRADE, every member ────────────────────────────────────────────────
check("FRAMEWORK_AUDIT_GRADE is a non-empty dict", len(FRAMEWORK_AUDIT_GRADE) >= 1,
      str(len(FRAMEWORK_AUDIT_GRADE)))
print(f"    {len(FRAMEWORK_AUDIT_GRADE)} frameworks: {sorted(FRAMEWORK_AUDIT_GRADE)}")

bad_grade, weak, no_roundtrip = [], [], []
for fw, g in FRAMEWORK_AUDIT_GRADE.items():
    if g not in GRADES: bad_grade.append((fw, g))
    if _rank(g) < DURABLE: weak.append((fw, g))          # "none" = no audit requirement = a bug here
    if required_grade([fw])[0] != g: no_roundtrip.append((fw, required_grade([fw])[0], g))

check("every framework maps to a VALID grade (in GRADES)", not bad_grade, str(bad_grade))
check("every framework is AUDIT-BEARING (>= durable, never none)  [downgrade-to-none guard]",
      not weak, f"these frameworks require no audit grade: {weak}")
check("the resolver round-trips every framework key", not no_roundtrip, str(no_roundtrip))

# highest-wins holds across EVERY pair, not just the hand-listed triple.
pair_fail = []
keys = sorted(FRAMEWORK_AUDIT_GRADE)
for i, a in enumerate(keys):
    for b in keys[i:]:
        want = stronger(FRAMEWORK_AUDIT_GRADE[a], FRAMEWORK_AUDIT_GRADE[b])
        got = required_grade([a, b])[0]
        if got != want: pair_fail.append((a, b, got, want))
check("required_grade of any pair == the stronger of the two (highest-wins, all pairs)",
      not pair_fail, str(pair_fail[:5]))

check("an unknown framework is surfaced, not silently dropped",
      required_grade(['definitely_not_a_framework'])[1] == ['definitely_not_a_framework'],
      str(required_grade(['definitely_not_a_framework'])))

# ── F6: UNRATIFIED_MAPPINGS, every member ──────────────────────────────────────────────────
check("UNRATIFIED_MAPPINGS is non-empty", len(UNRATIFIED_MAPPINGS) >= 1, str(UNRATIFIED_MAPPINGS))
not_a_framework = [u for u in UNRATIFIED_MAPPINGS if u not in FRAMEWORK_AUDIT_GRADE]
check("every unratified name is a real framework in the table", not not_a_framework,
      str(not_a_framework))
not_flagged = [u for u in UNRATIFIED_MAPPINGS if u not in required_grade([u], return_unratified=True)[2]]
check("required_grade flags every unratified framework (flagging mechanism works)",
      not not_flagged, f"these unratified frameworks were NOT flagged: {not_flagged}")

# ── F4: GRADES ladder ──────────────────────────────────────────────────────────────────────
ranks = [_rank(g) for g in GRADES]
check("_rank is strictly increasing across the GRADES ladder", ranks == sorted(set(ranks)),
      str(list(zip(GRADES, ranks))))
sat_fail = [(a, b) for a in GRADES for b in GRADES if satisfies(a, b) != (_rank(a) >= _rank(b))]
check("satisfies(a,b) is exactly rank(a) >= rank(b) for every grade pair", not sat_fail, str(sat_fail))
str_fail = [(a, b) for a in GRADES for b in GRADES
            if stronger(a, b) != (a if _rank(a) >= _rank(b) else b)]
check("stronger(a,b) is the higher-ranked grade for every pair", not str_fail, str(str_fail))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
