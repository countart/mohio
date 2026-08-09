# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: a dotted field access whose field name is a type-word -- e.g. `msg.text`,
`msg.int` -- must parse and resolve at runtime.

Background: the field name `text` collides with the `text` type terminal, so a raw
Lark parse of `msg.text` fails. The pretokenizer marks dotted user-var accesses as a
single USERVAR_DOTTED token, which sidesteps the collision (and restores the O(1)
dotted-name speed). This guard proves:

  1. Raw parse (no pretokenizer) of `msg.text` FAILS -- so the pretokenizer is
     load-bearing here, not decorative. If this ever starts passing on its own,
     the grammar changed and this guard should be revisited.
  2. Through the real path (pretokenize -> parse -> transform -> serve), a POST body
     carrying a `text` field resolves through `msg.text` to the actual value over a
     live TestClient round-trip.
  3. A declared field on the same instance (`msg.body`) still resolves (control).
"""
import os, sys, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient
from mohio_symbol_table import extract_symbols
from mohio_transformer import MOHIO_RESERVED_EXACT
from mohio_pretokenizer import pretokenize

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

def _parse_real(src):
    """Mirror mio.py _parse_and_validate: pretokenize, then parse + transform."""
    st = extract_symbols(src)
    psrc = pretokenize(src, st.all_user_names(), MOHIO_RESERVED_EXACT)
    return transform(_P.parse(psrc), psrc)

def unwrap(r):
    try:
        data = r.json()
    except Exception:
        return r.text.strip()
    if isinstance(data, list):
        return data
    msg = data.get("message", data.get("body", ""))
    if isinstance(msg, str):
        m = re.match(r"MohioValue\('(.+?)',", msg)
        if m:
            return m.group(1)
    return msg

PROG = (
    "shape Msg\n"
    "    body as text\n"
    "shape: done\n"
    "\n"
    "listen for\n"
    "    new sh.Msg at /echo\n"
    "        give back {FIELD}\n"
    "    new: done\n"
    "listen: done\n"
)

def serve_field(field):
    src = PROG.replace("{FIELD}", field)
    try:
        prog = _parse_real(src)
    except Exception as e:
        return -1, f"PARSE: {str(e).splitlines()[0][:80]}"
    try:
        server = MohioServer(prog, MohioInterpreter())
        c = TestClient(create_app(server), raise_server_exceptions=False)
        r = c.post("/echo", json={"body": "a body", "text": "HELLO"})
        return r.status_code, unwrap(r)
    except Exception as e:
        return -2, f"RUNTIME: {str(e).splitlines()[0][:80]}"

print("test_dotted_text_field")

# 1. Raw parse (no pretokenizer) of msg.text must fail -- proves the pretokenizer
#    is load-bearing. A control (msg.body) must parse raw.
raw_text_ok = True
try:
    transform(_P.parse("give back msg.text"), "give back msg.text")
except Exception:
    raw_text_ok = False
check("raw parse of msg.text fails without pretokenizer", raw_text_ok, False)

raw_body_ok = True
try:
    transform(_P.parse("give back msg.body"), "give back msg.body")
except Exception:
    raw_body_ok = False
check("raw parse of msg.body succeeds (control)", raw_body_ok, True)

# 2. Through the real path, msg.text resolves to the POSTed value.
code, val = serve_field("msg.text")
check("serve msg.text status", code, 200)
check("serve msg.text resolves POSTed value", val, "HELLO")

# 3. A declared field on the same instance still resolves (control).
code_b, val_b = serve_field("msg.body")
check("serve msg.body status", code_b, 200)
check("serve msg.body resolves POSTed value", val_b, "a body")

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
