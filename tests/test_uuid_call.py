# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Guard: uuid() must return a UUID. It used to return a TIMESTAMP.

The grammar admits both now() and uuid() under `time_expr`:

    time_expr: NOW_CALL (("-"|"+") duration_expr)?
             | UUID_CALL

The transformer named only NOW_CALL and let everything else fall through to
`TimeExpr(base=...)`, so uuid() was evaluated as the current time:

    hold u uuid()      ->      u = "2026-07-14T20:36:18.842854"

It parsed. It checked clean. It ran. It silently produced the WRONG KIND OF VALUE. A "uuid"
that is really a timestamp is PREDICTABLE and COLLIDABLE, so anything using it for a session
token, a password-reset link, or a key had a security hole and no warning at all.

The bitter part: the interpreter has ALWAYS had a correct evaluator --

    if isinstance(node, UuidCall):
        return MohioValue(str(uuid.uuid4()), 'uuid')

-- and it was never reached, because `UuidCall` was never imported into the transformer, so
nothing could build one. Both ends of the feature were finished. Nobody connected them.

That is the same failure as ai.decide's confidence threshold sitting at a hardcoded 0.85
while the developer's `check confidence above 0.9` was thrown away. A list that does not
name a thing does not fail -- it silently substitutes something else.

These tests exist so neither can come back.
"""
import os, sys, re, io, inspect, contextlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import UuidCall, TimeExpr

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          got: {detail}")
    _p += bool(cond); _f += (not cond)


def ast_of(src):
    return transform(_P.parse(src), src)


def shown(src):
    """Run end to end and read what `show` actually produced.

    `show` appends to `interpreter.shown`; it does not print. Capturing stdout returned ''
    and made every assertion below "pass" for the wrong reason -- a test that exercises no
    code path. Read the real output.
    """
    it = MohioInterpreter()
    it.run(ast_of(src))
    return (it.shown[-1] if it.shown else '').strip()


# --- the node -------------------------------------------------------------------------
node = ast_of('hold u uuid()\n').statements[0].value
check("uuid() builds a UuidCall, not a TimeExpr",
      isinstance(node, UuidCall),
      f"{type(node).__name__} -- if this says TimeExpr, uuid() is a timestamp again")

node = ast_of('hold t now()\n').statements[0].value
check("now() still builds a TimeExpr (the fix did not break time)",
      isinstance(node, TimeExpr), type(node).__name__)


# --- the value the developer actually gets --------------------------------------------
u = shown('hold u uuid()\nshow u\n')
check("uuid() returns a real UUID", bool(UUID_RE.match(u)), repr(u))
check("uuid() does NOT return a timestamp", not TS_RE.match(u), repr(u))

t = shown('hold t now()\nshow t\n')
check("now() still returns a timestamp", bool(TS_RE.match(t)), repr(t))

a = shown('hold a uuid()\nshow a\n')
b = shown('hold b uuid()\nshow b\n')
check("two uuid() calls differ (a timestamp collides; a uuid does not)",
      a != b and bool(UUID_RE.match(a)) and bool(UUID_RE.match(b)),
      f"{a!r} vs {b!r}")

# uuid() used the way it is most dangerous to get wrong: as a token, inside a string.
# `unique.id` inside `&` once silently became an empty string in exactly this position.
tok = shown('hold u uuid()\nshow ("token-" & u)\n')
check("uuid() survives string concatenation (does not vanish)",
      tok.startswith('token-') and bool(UUID_RE.match(tok[len('token-'):])),
      repr(tok))


# --- the fallthrough that caused it ---------------------------------------------------
body = inspect.getsource(transform.__globals__['MohioTransformer'].time_expr)
check("time_expr REFUSES an unnamed token instead of guessing a timestamp",
      'raise' in body and 'return TimeExpr(base=base)' not in body,
      "time_expr still has a silent fallback: the next token added to that rule will "
      "quietly become a timestamp, exactly as uuid() did")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
