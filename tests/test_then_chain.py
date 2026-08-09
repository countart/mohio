# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Regression for the `then` result-threading pipeline.

`then` carries the preceding result forward as `it`, so a chain reads like a
recipe: head produces a value, each `then` step runs on `it` and rebinds it.
  - transforming steps update `it`; side-effect steps (None result) pass through
  - `it` is the running result, usable anywhere a value is
  - a failing step fails loud: WHICH step (N of M), the reason, line, and a hint

Also guards the closer-leak regression: adding then_chain to `statement`
destabilized Earley's resolution of create_block's generic closer; create_block.2
priority restores it. A create block followed by `give back` must still parse.

Verified by RUNNING (parse-OK != runtime-OK).
"""
import os, sys
from pathlib import Path
from lark import Lark
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mohio_data
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


def check_contains(label, got, needle):
    global _passed, _failed
    if isinstance(got, str) and needle in got:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: {got!r} did not contain {needle!r}")


def _parser():
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    return Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)


_P = _parser()


def run(body):
    prog = ("shape S\n    method POST\nshape: done\n"
            "listen for\n    new sh.S at /x\n"
            + "\n".join("        " + l for l in body.splitlines())
            + "\n    new: done\nlisten: done\n")
    r = MohioInterpreter().run(transform(_P.parse(prog), prog),
                               request={"_method": "POST", "_path": "/x"})
    v = getattr(r, "value", r)
    return v.get("body") if hasattr(v, "get") else v


def parses(body):
    """True if the program parses + transforms without raising."""
    prog = ("shape S\n    method POST\nshape: done\n"
            "listen for\n    new sh.S at /x\n"
            + "\n".join("        " + l for l in body.splitlines())
            + "\n    new: done\nlisten: done\n")
    try:
        transform(_P.parse(prog), prog)
        return True
    except Exception:
        return False


def test_numeric_threading():
    print("\n=== threads a numeric result forward via `it` ===")
    check("10 -> +5 -> +100 = 115",
          run("n 10\n    then (it + 5)\n    then (it + 100)\ngive back it"),
          115)


def test_string_threading():
    print("\n=== threads a string result forward via `it` ===")
    # "hello world" -> before " " -> "hello" -> after "el" -> "lo"
    check("string transforms chain on it",
          run('greeting "hello world"\n    then it before " "\n'
              '    then it after "el"\ngive back it'),
          "lo")


def test_value_head():
    print("\n=== a bare value can be the chain head (no naming needed) ===")
    check("value head threads",
          run('"hello world"\n    then it before " "\ngive back it'),
          "hello")


def test_single_then():
    print("\n=== a single then step works ===")
    check("one step",
          run('word "hello"\n    then it before "llo"\ngive back it'),
          "he")


def test_fail_loud_names_the_step():
    print("\n=== a failing step fails loud with which step + reason ===")
    # step 3 divides by zero -> chain surfaces the failure, naming the step
    out = run("n 10\n    then (it + 5)\n    then (it / 0)\ngive back it")
    out_s = out if isinstance(out, str) else str(out)
    check_contains("error names step 3 of 3", out_s,
                   "step 3 of 3 in the 'then' pipeline failed")
    check_contains("error carries the reason", out_s, "divide by zero")


def test_lone_statement_not_hijacked():
    print("\n=== a statement with no `then` is NOT treated as a chain ===")
    check("plain assignment still works",
          run('greeting "hi"\ngive back greeting'), "hi")


def test_create_closer_regression():
    print("\n=== create block + give back still parses (closer-leak guard) ===")
    ok = parses('create Report\n    title "x"\n    errors 0\ncreate: done\n'
                'give back report as.json')
    check("create+giveback parses", ok, True)


if __name__ == "__main__":
    test_numeric_threading()
    test_string_threading()
    test_value_head()
    test_single_then()
    test_fail_loud_names_the_step()
    test_lone_statement_not_hijacked()
    test_create_closer_regression()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
