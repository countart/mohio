# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): AI-governance compliance gates in
mohio_transformer.py (_v_ai_decide_block, _v_ai_resolve_block, _v_ai_agent_block) did raw
`'keyword' in tree_to_str(tree)` substring searches -- which include the CONTENTS of every
quoted STRING literal in the block verbatim. An unrelated string value that happened to
contain the phrase ("not confident", "ai.audit", "cache"/"learned"/"live", "limits") silently
satisfied a compliance-critical check with no real construct present.

Fixed: strip_quoted_string_contents() blanks quoted-string contents (keeps quote marks, so
the separate quote-counting fallback in ai.agent's `goal` check is unaffected) before these
specific substring checks run.

UPDATE (T1-SILENT-SWEEP-BATCH7, same session, later phase): the ai.resolve false-POSITIVE
noted below at the time as "not fixed here" IS now fixed, in the same follow-on batch --
_v_ai_resolve_block was rewritten to check the actual TREE STRUCTURE (find_subtree for the
aliased resolve_cache/resolve_learned/resolve_live nodes) instead of substring-matching text
at all, which both closes the false-positive AND supersedes the scrubbing fix for this one
check (a quoted string can never produce a resolve_cache/resolve_learned/resolve_live
subtree, so the spoofing vector is closed structurally, not just textually). Covered below.

NOTE on remaining scope: one SEPARATE, PRE-EXISTING defect was found while verifying item 13
and is still deliberately NOT covered/fixed here (out of scope, logged separately for
attended review -- BUG-AI-AGENT-GOAL-QUOTECOUNT in PRODUCTION-BUILD-PLAN.md): ai.agent's
has_goal check has its OWN pre-existing, much looser fallback (`text.count('"') >= 2` -- ANY
quoted string anywhere in the block, unrelated to goal, satisfies it) that this fix does not
touch or tighten -- it is security-adjacent and needs a strictness ruling, not an unattended
guess.

Run: `python tests/test_ai_governance_quote_spoofing.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer import validate

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

def run(src):
    tree = P.parse(src)
    return validate(tree, source=src)


# ── ai.decide: not confident / ai.audit ─────────────────────────────────────────────
SPOOFED_DECIDE = (
    "ai.decide isSuspect returns boolean\n"
    "    check confidence above 0.85\n"
    "    weigh transaction.amount\n"
    "    give back true\n"
    "        with note \"not confident, but ai.audit says we should ship anyway\"\n"
    "ai.decide: done\n")
ctx = run(SPOOFED_DECIDE)
errs = [str(e) for e in ctx.errors]
warns = [str(w) for w in ctx.warnings]
check("ai.decide: a quoted string containing 'not confident' does NOT satisfy the real check",
      any('missing' in e.lower() and 'not confident' in e.lower() for e in errs), errs)
check("ai.decide: a quoted string containing 'ai.audit' does NOT satisfy the real check",
      any('no' in w.lower() and 'ai.audit' in w.lower() for w in warns), warns)

REAL_DECIDE = (
    "ai.decide isSuspect2 returns boolean\n"
    "    check confidence above 0.85\n"
    "    weigh transaction.amount\n"
    "    ai.audit to fraud_audit_log\n"
    "    not confident\n"
    "        give back pending \"Sent to a human\"\n"
    "ai.decide: done\n")
ctx2 = run(REAL_DECIDE)
check("ai.decide: a REAL not confident + ai.audit produces no false positive",
      not ctx2.errors and not ctx2.warnings,
      ([str(e) for e in ctx2.errors], [str(w) for w in ctx2.warnings]))

# ── ai.resolve: cache / learned / live (presence-of-spoofing only, see module docstring) ──
SPOOFED_RESOLVE = (
    "ai.resolve pick\n"
    "    model \"cache learned live -- fake tier note, not real tokens\"\n"
    "ai.resolve: done\n")
ctx3 = run(SPOOFED_RESOLVE)
errs3 = [str(e) for e in ctx3.errors]
check("ai.resolve: a quoted string mentioning cache/learned/live does NOT satisfy the check",
      any('is missing' in e.lower() and 'cache' in e.lower() for e in errs3), errs3)

REAL_RESOLVE = (
    "ai.decide checkFraud returns boolean\n"
    "    check confidence above 0.85\n"
    "    weigh transaction.amount\n"
    "    not confident\n"
    "        give back pending \"review\"\n"
    "ai.decide: done\n\n"
    "ai.resolve pick2\n"
    "    cache lookup_cache\n"
    "    learned db.learned_decisions\n"
    "    live ai.decide checkFraud\n"
    "ai.resolve: done\n")
ctx3b = run(REAL_RESOLVE)
errs3b = [str(e) for e in ctx3b.errors if 'ai.resolve' in str(e).lower()]
check("ai.resolve: a REAL cache+learned+live declaration produces no false positive "
      "(was BROKEN: underscore-filtered terminals made this unreachable via text-matching)",
      not errs3b, errs3b)

MISSING_LIVE_RESOLVE = (
    "ai.resolve pick3\n"
    "    cache lookup_cache\n"
    "    learned db.learned_decisions\n"
    "ai.resolve: done\n")
ctx3c = run(MISSING_LIVE_RESOLVE)
errs3c = [str(e) for e in ctx3c.errors if 'ai.resolve' in str(e).lower()]
check("ai.resolve: a genuinely missing tier is still correctly flagged "
      "(not over-corrected into a silent pass)",
      any('missing' in e.lower() and 'live' in e.lower() for e in errs3c), errs3c)

# ── ai.agent: limits / not confident ────────────────────────────────────────────────
SPOOFED_AGENT = (
    "ai.agent helper\n"
    "    context \"no limits declared, and not confident this agent is safe to run\"\n"
    "ai.agent: done\n")
ctx4 = run(SPOOFED_AGENT)
errs4 = [str(e) for e in ctx4.errors]
warns4 = [str(w) for w in ctx4.warnings]
check("ai.agent: a quoted string mentioning 'limits' does NOT satisfy the real check",
      any('missing' in e.lower() and 'limits' in e.lower() for e in errs4), errs4)
check("ai.agent: a quoted string mentioning 'not confident' does NOT satisfy the real check",
      any('not confident' in w.lower() for w in warns4), warns4)

REAL_AGENT = (
    "ai.agent helper2\n"
    "    goal \"help the user\"\n"
    "    limits\n"
    "        max steps 5\n"
    "    limits: done\n"
    "ai.agent: done\n")
ctx5 = run(REAL_AGENT)
errs5 = [str(e) for e in ctx5.errors if 'ai.agent' in str(e).lower()]
check("ai.agent: a REAL goal + limits produces no false positive", not errs5, errs5)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
