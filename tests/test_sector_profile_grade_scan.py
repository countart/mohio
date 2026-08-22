# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): scan_audit_grade_requirement's sector-profile
lookup was broken -- it correctly resolved a sector name to a real file PATH via
find_sector_profile(name), but then passed that PATH into load_sector_profile(), which
expects a NAME (it re-resolves to a path internally via its own find_sector_profile call).
A path can never match a real filename, so load_sector_profile() always returned None,
`frameworks` stayed permanently [], and the WORM/append-only audit-grade warning below
never fired for any real sector with a real profile -- confirmed via
`sector-demo-regulated.sector` (compliance: hipaa -> append_only), a real, shipped profile.

Fixed: pass the sector NAME, not the path, matching what load_sector_profile expects.

Run: `python tests/test_sector_profile_grade_scan.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_reachability import scan_audit_grade_requirement

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

def scan(sector_name):
    src = f'sector: {sector_name}\n'
    prog = transform(P.parse(src), src)
    return scan_audit_grade_requirement(prog)


# A real, shipped profile requiring append_only (compliance: hipaa) -- the exact
# reproduction the fix was verified against.
warnings = scan('demo-regulated')
grade_warnings = [w for w in warnings if 'requires' in w.message and 'audit storage' in w.message]
no_profile_warnings = [w for w in warnings if 'no profile for it was found' in w.message]
check("a sector with a real profile requiring append_only gets the grade warning",
      bool(grade_warnings), [w.message for w in warnings])
check("it does NOT also claim no profile was found (that would be a different, wrong bug)",
      not no_profile_warnings, [w.message for w in warnings])
if grade_warnings:
    check("the warning names the real framework (hipaa) and grade (append_only)",
          'hipaa' in grade_warnings[0].message and 'append_only' in grade_warnings[0].message,
          grade_warnings[0].message)

# Regression: a genuinely nonexistent sector still reports "no profile found", unaffected.
warnings2 = scan('totally-nonexistent-sector-name')
check("a sector with NO real profile still correctly reports 'no profile found' (regression guard)",
      any('no profile for it was found' in w.message for w in warnings2),
      [w.message for w in warnings2])

# Regression: sectors with real profiles but no append_only/worm requirement stay silent
# (no false positive from the fix).
for sname in ('demo-low', 'demo-high'):
    warnings3 = scan(sname)
    check(f"'{sname}' (no append_only/worm compliance requirement) produces no grade warning "
          f"(no false positive)",
          not any('requires' in w.message and 'audit storage' in w.message for w in warnings3),
          [w.message for w in warnings3])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
