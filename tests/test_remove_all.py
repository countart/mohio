# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Regression for `remove.all` (4th validator-dogfood gap).

  - `remove.all from db.<t>` is a bare one-liner that truncates the table.
    It previously REQUIRED a closer, so the one-liner couldn't form and fell
    back to a service call ("remove.all has no handler in this build").
  - `remove all` with a SPACE used to parse as two silent no-op assignments
    (remove = all; from = db.x) -- a dangerous footgun on a destructive op.
    Now it fails loud and points to the dotted form.
  (on.success / on.failure on remove.all is a future enhancement.)

Verified by RUNNING (parse-OK != runtime-OK).
"""
import os, sys
from pathlib import Path
from lark import Lark
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

os.environ.setdefault("DATABASE_URL", ":memory:")
_passed = _failed = 0


def check(label, got, expected):
    global _passed, _failed
    if got == expected:
        _passed += 1
        print(f"  [PASS] {label}: {got!r}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")


def _parser():
    raw = Path("mohio.lark").read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    return Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)


_P = _parser()
_SEED = ('save to db.items\n    name "a"\nsave: done\n'
         'save to db.items\n    name "b"\nsave: done\n')


def run(handler_body):
    prog = ("connect db as sqlite from env.DATABASE_URL\n"
            "shape S\n    method POST\nshape: done\n"
            "listen for\n    new sh.S at /x\n"
            + "\n".join("        " + l for l in handler_body.splitlines())
            + "\n    new: done\nlisten: done\n")
    r = MohioInterpreter().run(transform(_P.parse(prog), prog),
                               request={"_method": "POST", "_path": "/x"})
    v = getattr(r, "value", r)
    return v.get("body") if hasattr(v, "get") else v


def rows_after(action):
    body = run(_SEED + action + "find r in db.items\nfind: done\ngive back r")
    return len(body) if isinstance(body, list) else body


def test_remove_all_truncates():
    print("\n=== remove.all clears the whole table ===")
    check("control: 2 rows seeded", rows_after(""), 2)
    check("remove.all -> 0 rows", rows_after("remove.all from db.items\n"), 0)


def test_remove_all_block_on_success():
    print("\n=== remove.all block form runs on.success (no handler closer) ===")
    # handler self-delimits; the block closes with canonical 'remove: done'
    body = run(_SEED + "remove.all from db.items\n"
               "    on.success\n        give back \"cleared\"\n"
               "remove: done\ngive back \"noreach\"")
    check("on.success fires; closed by canonical 'remove: done'", body, "cleared")


def test_spaced_remove_all_fails_loud():
    print("\n=== spaced `remove all` fails loud (footgun guard) ===")
    raised = False
    msg = ""
    try:
        run(_SEED + "remove all from db.items\ngive back \"x\"")
    except Exception as e:
        raised, msg = True, str(e)
    check("spaced form raises", raised, True)
    check("message points to remove.all", "remove.all from db" in msg, True)
    check("message names the silent-delete risk",
          "silently delete nothing" in msg, True)


def test_closer_forms():
    print("\n=== closer rule: verb / verb.modifier / bare done all close; cross-verb fails ===")
    def parses(action):
        prog = ("connect db as sqlite from env.DATABASE_URL\n"
                "shape S\n    method POST\nshape: done\n"
                "listen for\n    new sh.S at /x\n"
                + "\n".join("        " + l for l in (action + "give back r").splitlines())
                + "\n    new: done\nlisten: done\n")
        try:
            transform(_P.parse(prog), prog); return True
        except Exception:
            return False
    check("find ... find: done (canonical)", parses("find r in db.items\nfind: done\n"), True)
    check("find ... done (bare)", parses("find r in db.items\ndone\n"), True)
    check("find ... save: done (cross-verb) fails", parses("find r in db.items\nsave: done\n"), False)


if __name__ == "__main__":
    test_remove_all_truncates()
    test_remove_all_block_on_success()
    test_spaced_remove_all_fails_loud()
    test_closer_forms()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
