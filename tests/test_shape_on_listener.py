# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Shape-on-listener: `listen for sh.X [at /path]` binds the shape directly.

Decision 1: the shape binds on the listener; the body IS the handler; `listen: done`
closes it. No `new` wrapper required. The `new` wrapper still parses and routes
(back-compat) until Zork is migrated. Verified by RUNNING requests through the
interpreter (parse-OK != runtime-OK).
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", ":memory:")

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_passed = _failed = 0
def check(label, got, expected):
    global _passed, _failed
    if got == expected:
        _passed += 1; print(f"  [PASS] {label}: {got!r}")
    else:
        _failed += 1; print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")

def parses(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1; print(f"  [PASS] {label}")
    else:
        _failed += 1; print(f"  [FAIL] {label}  {detail}")


def _parser():
    raw = Path("mohio.lark").read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    return Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)
_P = _parser()

_SHAPE = "shape S\n    method POST\nshape: done\n"

def _run(prog, path="/x"):
    r = MohioInterpreter().run(transform(_P.parse(prog), prog),
                               request={"_method": "POST", "_path": path})
    v = getattr(r, "value", r)
    return v.get("body") if hasattr(v, "get") else v

def _builds(prog):
    try:
        transform(_P.parse(prog), prog); return True, ""
    except Exception as e:
        return False, str(e).splitlines()[0][:70]


def test_shape_on_listener_with_path():
    print("\n=== listen for sh.X at /path routes the handler ===")
    p = _SHAPE + 'listen for sh.S at /x\n    give back 200 "bound"\nlisten: done\n'
    check("routes to handler", _run(p), "bound")

def test_shape_on_listener_no_path():
    print("\n=== listen for sh.X (no path) routes by shape ===")
    p = _SHAPE + 'listen for sh.S\n    give back 200 "by-shape"\nlisten: done\n'
    check("routes by shape", _run(p), "by-shape")

def test_new_wrapper_back_compat():
    print("\n=== new wrapper still routes (back-compat until Zork migrates) ===")
    p = (_SHAPE + 'listen for\n    new sh.S at /x\n'
         '        give back 200 "wrapper"\n    new: done\nlisten: done\n')
    check("new wrapper routes", _run(p), "wrapper")

def test_shape_on_listener_with_try_inside():
    print("\n=== zork-shaped: shape-on-listener with a try block inside parses ===")
    p = (_SHAPE + 'listen for sh.S\n'
         '    try up to 2 times\n'
         '        give back 200 "ok"\n'
         '    on.failure\n'
         '        give back 500 "err"\n'
         '    always\n'
         '        miolog.info "done"\n'
         '    try: done\n'
         'listen: done\n')
    ok, detail = _builds(p)
    parses("parses with try+modifier+on.failure+always inside", ok, detail)


if __name__ == "__main__":
    test_shape_on_listener_with_path()
    test_shape_on_listener_no_path()
    test_new_wrapper_back_compat()
    test_shape_on_listener_with_try_inside()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
