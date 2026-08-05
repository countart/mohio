# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Parity predicates `n is even` / `n is odd` (fix for the silent-false bug).

Before: `even`/`odd` were not operators, so `2 is even` parsed as `2 is <undefined var even>`,
compared 2 to None, and silently returned false -- surfaced when a conditional `add` inside a loop
produced []. Now they are real unary predicates on a WHOLE number; a non-integer fails loud rather
than answering a bad question with a silent false.

Verified by running (parse-OK is not runtime-OK for this class of bug)."""
import os, sys
from pathlib import Path
from lark import Lark

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", ":memory:")

from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

_raw = Path("mohio.lark").read_text(encoding="utf-8")
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

_p = _f = 0
def check(label, got, expected):
    global _p, _f
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        print(f"          expected {expected!r}")
    _p += ok; _f += (not ok)


def result(body):
    prog = ("shape S\n    method POST\nshape: done\n"
            "listen for\n    new sh.S at /x\n"
            + "\n".join("        " + l for l in body.splitlines())
            + "\n    new: done\nlisten: done\n")
    r = MohioInterpreter().run(transform(_P.parse(prog), prog),
                               request={"_method": "POST", "_path": "/x"})
    v = getattr(r, "value", r)
    return v.get("body") if hasattr(v, "get") else v


def parity(n_literal, pred):
    return result(f'verdict "?"\ncheck {n_literal}\n    when {n_literal} is {pred}\n'
                  f'        verdict "hit"\n    otherwise\n        verdict "miss"\n'
                  f'check: done\ngive back verdict')


print("\n=== parity computes ===")
check("2 is even", parity("2", "even"), "hit")
check("4 is even", parity("4", "even"), "hit")
check("3 is even -> otherwise", parity("3", "even"), "miss")
check("3 is odd", parity("3", "odd"), "hit")
check("0 is even", parity("0", "even"), "hit")
check("2 is odd -> otherwise", parity("2", "odd"), "miss")

print("\n=== the loop case that originally returned [] ===")
check("evens filtered out of a loop",
      result('nums as list 1, 2, 3, 4\nevens as list text\n'
             'repeat each n in nums\n    check n\n        when n is even\n'
             '            add n to evens\n    check: done\nrepeat: done\ngive back evens'),
      [2, 4])

print("\n=== non-integers FAIL LOUD (never silent-false) ===")
def fails_loud(label, body, phrase):
    global _p, _f
    raised = False
    msg = ""
    try:
        result(body)
    except Exception as e:
        raised, msg = True, str(e)
    ok = raised and phrase in msg
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: raised={raised}")
    if not ok:
        print(f"          msg={msg!r}")
    _p += ok; _f += (not ok)

fails_loud("2.5 is even fails loud",
           'check 2.5\n    when 2.5 is even\n        verdict "x"\ncheck: done', "whole number")
fails_loud("'abc' is even fails loud",
           'x "abc"\ncheck x\n    when x is even\n        verdict "x"\ncheck: done', "whole number")

print("\n=== the wider silent-false trap: `is <undefined word>` fails loud ===")
fails_loud("`2 is frobnicate` (undefined word) fails loud",
           'check 2\n    when 2 is frobnicate\n        verdict "x"\ncheck: done', "unknown name")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
