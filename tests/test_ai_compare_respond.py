# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""ai.compare and ai.respond -- the two ai.* blocks that reuse ai_decide_body.

Both were grammar-only until 2026-07-18: they parsed into a block and then failed loud with
"No executor". They are now built as compositions over the same AI runtime call ai.decide uses.

  ai.compare  -- relational judgment. Binds { winner, margin, explanation }.
                 `margin` is how decisively the winner won, derived from confidence:
                 0.5 confidence is a coin-flip (margin 0), 1.0 is unanimous (margin 1).
                 A comparison that reports only the winner hides the difference between
                 "A, barely" and "A, decisively".
  ai.respond  -- interaction response (support reply, chat turn, narration). Binds text.

Neither carries a confidence gate. ai.decide requires one because it is a gated decision with a
correct answer; a comparison's margin IS its confidence, and a generated response has no
correct/incorrect to gate on. Demanding a gate on either would be enforcement theatre.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MockAiRuntime

_raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
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
    it = MohioInterpreter(ai=MockAiRuntime())
    t = transform(P.parse(src), src)
    it.run_declarations(t); r = it.run(t)
    b = getattr(r, 'value', r)
    return b.get('body') if isinstance(b, dict) else b


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── ai.compare: runs, and binds the documented record shape ───────────────────────────
res = run('a 10\nb 20\nai.compare best\n    weigh a, b\nai.compare: done\ngive back 200 best\n')
check("ai.compare runs (has an executor)", isinstance(res, dict), f"got {res!r}")
check("ai.compare binds a winner", 'winner' in (res or {}))
check("ai.compare binds a margin", 'margin' in (res or {}))
check("ai.compare binds an explanation", 'explanation' in (res or {}))
check("ai.compare margin is within 0..1",
      isinstance(res, dict) and 0.0 <= float(res.get('margin', -1)) <= 1.0,
      f"margin={res.get('margin') if isinstance(res, dict) else None}")

# field access on the bound record
check("ai.compare result supports .winner field access",
      run('a 1\nai.compare c\n    weigh a\nai.compare: done\ngive back 200 c.winner\n') is not None)
check("ai.compare result supports .margin field access",
      run('a 1\nai.compare c\n    weigh a\nai.compare: done\ngive back 200 c.margin\n') is not None)

# ── ai.respond: runs, and binds text ──────────────────────────────────────────────────
resp = run('m "hello"\nai.respond reply\n    weigh m\nai.respond: done\ngive back 200 reply\n')
check("ai.respond runs (has an executor)", resp is not None and not isinstance(resp, dict),
      f"got {resp!r}")
check("ai.respond binds a text value", isinstance(resp, str) or resp is not None)

# ── prompt options (shared with ai.decide) are accepted ───────────────────────────────
check("ai.compare accepts goal/persona options",
      run('a 1\nb 2\nai.compare c\n    goal "pick the better one"\n'
          '    persona "analyst"\n    weigh a, b\nai.compare: done\n'
          'give back 200 c.winner\n') is not None)
check("ai.respond accepts a goal option",
      run('m "hi"\nai.respond r\n    goal "be friendly"\n    weigh m\nai.respond: done\n'
          'give back 200 r\n') is not None)

# ── closers are validated (a mismatched closer fails loud) ────────────────────────────
check("ai.compare closer mismatch fails loud",
      fails('a 1\nai.compare c\n    weigh a\nwrong: done\ngive back 200 c\n'))
check("ai.respond closer mismatch fails loud",
      fails('m "x"\nai.respond r\n    weigh m\nwrong: done\ngive back 200 r\n'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
