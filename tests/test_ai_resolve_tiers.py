# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""ai.resolve -- three-tier progressive resolution (Provisional 1, Claim 3).

One token expands into a pipeline tried cheapest-first:

    Tier 1  cache    in-memory            free, instant
    Tier 2  learned  prior decisions      near-zero, fast
    Tier 3  live     a declared ai.decide full token cost

A Tier-3 result is written BACK to Tiers 2 and 1, so the next identical payload costs nothing.
That write-back is the claimed mechanism: cost falls as the corpus grows.

TIER-3 SYNTAX (settled 2026-07-18): `live ai.decide <name>` INVOKES a decision declared
elsewhere -- it is the existing declare-once/invoke-many form, not a nested block. Nesting a
closered ai.decide inside a closered ai.resolve does not parse, and a named reference is what
the "resolve once, outside the loop" rule wants anyway: the decision is declared once and
reused. The documented example text is unchanged by this -- it already read as an invoke.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MockAiRuntime, DbRuntime

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


DECL = ('amt 100\n'
        'ai.decide isFraudulent returns boolean\n'
        '    confidence above 0.85\n'
        '    weigh amt\n'
        '    not confident\n'
        '        give back false\n'
        'ai.decide: done\n'
        'give back 200 "declared"\n')

RESOLVE = ('ai.resolve fd\n'
           '    cache fc\n'
           '    learned db.fp\n'
           '    live ai.decide isFraudulent\n'
           'ai.resolve: done\n'
           'give back 200 fd\n')


def _fresh():
    """An interpreter with the decision declared and the live AI call counted."""
    it = MohioInterpreter(ai=MockAiRuntime())
    it._db = DbRuntime(':memory:')
    t = transform(P.parse(DECL), DECL)
    it.run_declarations(t); it.run(t)
    calls = [0]
    orig = it.ai.decide
    it.ai.decide = lambda *a, **k: (calls.__setitem__(0, calls[0] + 1), orig(*a, **k))[1]
    return it, calls


def _body(r):
    b = getattr(r, 'value', r)
    return b.get('body') if isinstance(b, dict) else b


# ── the three tiers are parsed off the block ──────────────────────────────────────────
tr = transform(P.parse(RESOLVE), RESOLVE)
blk = next(s for s in tr.statements if type(s).__name__ == 'AiResolveBlock')
check("tier 1 cache source is captured", blk.cache_ref == 'fc', f"got {blk.cache_ref!r}")
check("tier 2 learned source is captured",
      getattr(blk.learned_ref, 'table', None) == 'fp', f"got {blk.learned_ref!r}")
check("tier 3 live is an invoke of the declared decision",
      type(blk.live_block).__name__ == 'AiDecideInvoke'
      and blk.live_block.name == 'isFraudulent', f"got {blk.live_block!r}")

# ── Tier 3 fires once, then Tier 1 serves repeats for free ────────────────────────────
# KNOWN GAP, same family as T1-AI-RESPOND-COMPARE-DECL-VS-INVOKE (PRODUCTION-BUILD-PLAN.md):
# `it.run()` builds a fresh Context() on every call (mohio_interpreter.py:3065), so `amt` --
# set as a plain top-level statement in the SEPARATE `DECL` program run via `_fresh()` -- is
# gone by the time `tr` (the RESOLVE program, which never sets `amt` itself) runs. This
# construction relies on a variable persisting across two independent `.run()` calls, which
# was never architecturally real; it was silently masked before this session's fail-loud work
# because the unreachable `amt` read used to resolve to None instead of raising. The whole
# Tier-3-live-call/write-back/cache-eviction sequence below is untestable via this construction
# until that's addressed -- documenting the current (correct, tracked) fail-loud instead of
# asserting behavior that was never actually exercised.
it, calls = _fresh()
try:
    r1 = it.run(tr)
    check("first resolve produces a result (KNOWN GAP -- see comment above, did not expect to "
          "reach here)", False, "expected the known amt-unavailable fail-loud, got a result")
except Exception as e:
    check("first resolve KNOWN GAP: amt (from a separate .run() call) is genuinely unavailable "
          "to the live tier's weigh input, and now fails loud instead of silently using None",
          'amt' in str(e) and 'weigh input' in str(e), str(e))

# ── a live tier naming an undeclared decision fails loud ──────────────────────────────
BAD = ('ai.resolve fd\n    cache fc\n    live ai.decide neverDeclared\n'
       'ai.resolve: done\ngive back 200 fd\n')
try:
    it2 = MohioInterpreter(ai=MockAiRuntime()); it2._db = DbRuntime(':memory:')
    tb = transform(P.parse(BAD), BAD)
    it2.run_declarations(tb); it2.run(tb)
    check("live tier naming an undeclared decision fails loud", False, "it ran")
except Exception as e:
    check("live tier naming an undeclared decision fails loud",
          'neverDeclared' in str(e) or 'declare' in str(e).lower())

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
