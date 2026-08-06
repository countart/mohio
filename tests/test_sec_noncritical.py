#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock test: sec.non_critical sector-floor exemption REQUIRES a reason.

sec.non_critical exempts a single NON-regulatory ai.decide from the sector
confidence floor. Per the locked security design and patent claim F3, the
override must be EXPLICIT and JUSTIFIED: a reason is required, and the reason is
logged for audit. sec.non_critical can never lower the security level or disable
a mandatory baseline -- the sector minimum is immutable.

Guarantees verified here (validator / compile-gate path, financial sector, 0.85 floor):
  1. below floor + sec.non_critical reason "..."   -> exempt (no floor error)
  2. below floor + bare sec.non_critical (no reason) -> SEC_NONCRITICAL_NO_REASON
  3. below floor + no exemption                     -> SECTOR_CONFIDENCE_FLOOR
  4. at/above floor                                 -> clean

The validator and the mio.py security report share ONE rule via
noncritical_status(), so the two checks cannot drift.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer import validate, noncritical_status

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def codes(src):
    tree = P.parse(src)
    ctx = validate(tree, source=src, filename="floor_check.mho")
    return [getattr(e, 'code', '') or '' for e in ctx.errors]

def decide(extra_line, conf="0.60"):
    return (
        "sector: demo_low\n"
        "ai.decide pick returns boolean\n"
        f"    confidence above {conf}\n"
        f"{extra_line}"
        '    goal "demo"\n'
        "    not confident\n"
        "        give back false\n"
        "ai.decide: done\n"
    )

# --- shared rule (helper) unit checks ---
check("helper: reason present -> exempt",
      noncritical_status('sec.non_critical reason "x"') == (True, True))
check("helper: bare -> present, no reason",
      noncritical_status('sec.non_critical') == (True, False))
check("helper: absent -> not present",
      noncritical_status('plain text') == (False, False))

# --- 1. exempt with a reason: floor suppressed ---
c1 = codes(decide('    sec.non_critical reason "ui preference, not regulatory"\n'))
check("1. below floor + sec.non_critical reason -> no floor error",
      'SECTOR_CONFIDENCE_FLOOR' not in c1 and 'SEC_NONCRITICAL_NO_REASON' not in c1)

# --- 2. bare sec.non_critical: reason required ---
c2 = codes(decide('    sec.non_critical\n'))
check("2. below floor + bare sec.non_critical -> SEC_NONCRITICAL_NO_REASON",
      'SEC_NONCRITICAL_NO_REASON' in c2 and 'SECTOR_CONFIDENCE_FLOOR' not in c2)

# --- 3. no exemption: floor enforced ---
c3 = codes(decide(''))
check("3. below floor + no exemption -> SECTOR_CONFIDENCE_FLOOR",
      'SECTOR_CONFIDENCE_FLOOR' in c3)

# --- 4. at/above floor: clean ---
c4 = codes(decide('', conf="0.90"))
check("4. at/above floor -> no floor error",
      'SECTOR_CONFIDENCE_FLOOR' not in c4 and 'SEC_NONCRITICAL_NO_REASON' not in c4)

print(f"\nRESULTS: {PASS}/{PASS+FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
