# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: a dotted value in an inline `on.failure` / `on.success` handler stays with the
`give back` instead of becoming a separate statement.

The grammar ignores newlines, so `give back 200 order.total` had two readings: the
value form, or the status-only form (`give back 200`) followed by a statement
`order.total`. Earley took the second. The value became a phantom ServiceCallStmt and
the `give back` was left with nothing, so the response body was empty -- every
puzzle-failure message in zork_demo was silently dropped, and the reachability scan
reporting "unreachable statement" was the only thing pointing at it.

The fix is that a bare dotted name is no longer a statement unless its head is a
reserved mio* service. `order.total` alone does nothing, so it is not an action;
`miosys.now` is. That removes the ambiguity at the source rather than teaching the
analyzer to ignore it.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

from lark import Lark
from dataclasses import fields, is_dataclass
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient
from mohio_reachability import scan_unreachable

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

_g = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _g.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

def handler_body(field):
    """Statement types inside the on.failure handler for `give back 200 r.<field>`."""
    src = ('page at /x\n    retrieve r from db.t\n        match id to 1\n'
           f'        on.failure give back 200 r.{field}\n    retrieve: done\npage: done\n')
    prog = transform(_P.parse(src), src)
    out = []
    def visit(n):
        if not is_dataclass(n):
            return
        if type(n).__name__ == 'OnFailure':
            out.append([type(i).__name__ for i in (getattr(n, 'body', None) or [])])
        for f in fields(n):
            v = getattr(n, f.name, None)
            if is_dataclass(v):
                visit(v)
            elif isinstance(v, list):
                for i in v:
                    visit(i)
    visit(prog.statements[0])
    return out[0] if out else []

print("test_handler_dotted_value")

# The value must remain part of the give back, whatever the field is called. These
# were ALL broken, not only the ones colliding with a keyword.
for fld in ("failure", "success", "description", "message", "id", "room", "total"):
    check(f"r.{fld} stays with the give back", handler_body(fld), ["GiveBackStmt"])

# A bare dotted name is no longer an action, but a real service call still is.
def parses(src):
    try:
        transform(_P.parse(src), src); return True
    except Exception:
        return False

check("bare mio* call still parses", parses('miosys.now'), True)
check("mio* call with a value still parses", parses('miolog.info "started"'), True)
check("mio* call with params still parses",
      parses('miocookie.set "theme" to "dark"'), True)

# End to end: the failure path returns the message, the success path falls through.
SRC = ('connect db as sqlite from env.DATABASE_URL\n'
       'page at /x\n'
       '    retrieve gate from db.puzzles\n'
       '        match id to "p1"\n'
       '        on.success\n'
       '            retrieve flag from db.flags\n'
       '                match flag_name to "nope"\n'
       '                on.failure give back 200 gate.failure\n'
       '            retrieve: done\n'
       '    retrieve: done\n'
       '    give back 200 "REACHED-AFTER"\n'
       'page: done\n')
prog = transform(_P.parse(SRC), SRC)

def serve(seed_flag):
    interp = MohioInterpreter()
    server = MohioServer(prog, interp)
    try:
        interp.run_declarations(prog)
    except Exception:
        pass
    conn = interp._db.conn
    conn.execute("CREATE TABLE IF NOT EXISTS puzzles (id TEXT, failure TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS flags (flag_name TEXT)")
    conn.execute("INSERT INTO puzzles VALUES ('p1','The gate is sealed.')")
    if seed_flag:
        conn.execute("INSERT INTO flags VALUES ('nope')")
    conn.commit()
    return TestClient(create_app(server), raise_server_exceptions=False).get("/x").text

check("the failure path returns the message",
      "The gate is sealed." in serve(seed_flag=False), True)
check("the success path falls through past both blocks",
      "REACHED-AFTER" in serve(seed_flag=True), True)

# The analyzer needed no change: with the phantom gone it is quiet here, and it still
# reports a genuinely unconditional return followed by a statement.
check("no unreachable warning on the nested handler",
      len(scan_unreachable(prog)), 0)
GENUINE = 'page at /x\n    give back 200 "first"\n    show "dead"\npage: done\n'
check("a real unreachable statement still warns",
      len(scan_unreachable(transform(_P.parse(GENUINE), GENUINE))) > 0, True)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
