# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The inline `max steps`/`max cost`/`max time` shorthand in ai.agent's body is retired
(2026-08-06, found during the A8 cost-cap investigation).

Confirmed empirically before retiring, not assumed: none of the three had any transformer
handling at all -- a raw, untransformed Tree fell into the generic unwired-construct scan.
Worse, mio check's own required-limits validation never recognized the shorthand as
satisfying "ai.agent needs a limits declaration" either, so no developer could ever have
used it successfully. The `limits` block is the one real, working form.

Grammar productions are KEPT (each alternative aliased with -> so the transformer can
tell "max steps" from "max cost" apart -- both reduce to an otherwise-identical bare
NUMBER once the underscore-filtered _MAX/_STEPS/_COST terminals are dropped, the same
technique limits_body already uses for its own five alternatives). Tested directly before
choosing this approach: removing the grammar alternatives instead (the run_block precedent)
produces a bare "No terminal matches" error with no redirect at all here -- run_block got
lucky with a coincidental fallback into an existing assignment-guard error; this construct
has no such natural fallback, so keeping the grammar and failing loud in the transformer
(the `hold` retirement's own precedent) is the fit that actually works.

Covers:
  - all three retired forms fail loud, each naming its OWN limits-block equivalent
    precisely (not a shared generic message) -- steps -> "max steps", cost -> "cost
    ceiling", time -> "timeout" (a different keyword, not "max time", in the limits block)
  - the real limits block form is completely unaffected (regression guard)
  - the retired forms are not silently accepted as unwired scaffolding (must be an error,
    not a warning)

Run: `python tests/test_ai_agent_shorthand_retired.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform, MohioCompileError
import mohio_data

_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def compile_err(src):
    try:
        transform(_P.parse(src), src)
        return None
    except MohioCompileError as e:
        return str(e)

AGENT = ('ai.agent triage\n    goal "x"\n    {clause}\n'
         '    not confident\n        give back 200 "x"\nai.agent: done\n')

# ── all three retired inline forms: fail loud, each naming its OWN redirect ────────
CASES = [
    ("max steps 10",     ["max steps", "limits", "10"]),
    ("max cost 5.00",    ["cost ceiling", "limits", "5.00"]),
    ("max time 30 seconds", ["timeout", "limits", "30", "seconds"]),
]
for clause, must_contain in CASES:
    e = compile_err(AGENT.format(clause=clause))
    check(f"`{clause}`: fails loud (MohioCompileError, not silently unwired)",
          e is not None, clause)
    for word in must_contain:
        check(f"`{clause}`: redirect message names '{word}'",
              e is not None and word in e, e or "")

# ── each message is DISTINCT -- not one generic "retired" string reused three times ──
e_steps = compile_err(AGENT.format(clause="max steps 10"))
e_cost  = compile_err(AGENT.format(clause="max cost 5.00"))
e_time  = compile_err(AGENT.format(clause="max time 30 seconds"))
check("steps and cost redirects are different messages (not a shared generic string)",
      e_steps != e_cost, (e_steps, e_cost))
check("cost and time redirects are different messages",
      e_cost != e_time, (e_cost, e_time))
check("the cost redirect correctly says 'cost ceiling', never the wrong 'max time' keyword",
      "cost ceiling" in (e_cost or "") and "max time" not in (e_cost or ""), e_cost)
check("the time redirect correctly says 'timeout', never the retired 'max time' phrasing "
      "as if it were the working keyword",
      "timeout" in (e_time or ""), e_time)

# ── regression: the real limits block form is completely unaffected ────────────────
LIMITS_OK = ('ai.agent triage\n    goal "x"\n'
             '    limits\n        max steps 10\n        cost ceiling 5.00\n'
             '        timeout 30 seconds\n    limits: done\n'
             '    not confident\n        give back 200 "x"\nai.agent: done\n')
check("the real `limits` block form (all three settings) still parses with no error "
      "(regression guard)",
      compile_err(LIMITS_OK) is None, compile_err(LIMITS_OK))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
