# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Regression for three gaps surfaced by dogfooding the world validator:
  GAP 1 — the object builder was dead at runtime (no transformer emitted the
          node) and the grammar greedily nested sibling fields. Now: flat
          multi-field `create` builds a full object and serializes via as.json.
          (The verb was 'make'; retired to 'create' -- 'make' now fails loud.)
  GAP 2 — `append`/`prepend` stringified list targets. Now: list target gets an
          element added (append=end, prepend=front); string path unchanged.
  GAP 3 — no empty growable list existed. Now: `NAME as list TYPE` (no value)
          declares an empty list to accumulate into.
Verified by RUNNING (parse-OK != runtime-OK is how these hid)."""
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
        print(f"  [PASS] {label}: {got}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")


def _parser():
    raw = Path("mohio.lark").read_text(encoding="utf-8")
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


def test_create_multifield_json():
    print("\n=== GAP 1: create builds a full multi-field object ===")
    body = run('create Report\n    title "World Validation"\n    errors 0\n'
               '    status "clean"\ncreate: done\ngive back report as.json')
    import json
    parsed = json.loads(body) if isinstance(body, str) else body
    check("create keeps all three fields", parsed,
          {"title": "World Validation", "errors": 0, "status": "clean"})


def test_make_is_retired():
    print("\n=== 'make' is retired -> fails loud, points to create ===")
    raised = False
    msg = ""
    try:
        run('make Report\n    title "x"\nmake: done\ngive back report')
    except Exception as e:
        raised, msg = True, str(e)
    check("make raises", raised, True)
    check("message points to create", "use 'create'" in msg, True)


def test_append_prepend_list():
    print("\n=== GAP 2: append/prepend add elements to a list ===")
    check("append element to list",
          run('colors as list text\nappend "red" to colors\n'
              'append "green" to colors\ngive back colors'),
          ["red", "green"])
    check("prepend element to list",
          run('colors as list text\nappend "red" to colors\n'
              'prepend "first" to colors\ngive back colors'),
          ["first", "red"])
    check("append to string still concatenates",
          run('hold name "report"\nappend ".pdf" to name\ngive back name'),
          "report.pdf")


def test_empty_growable_list():
    print("\n=== GAP 3: NAME as list TYPE declares an empty growable list ===")
    check("empty list starts empty",
          run('findings as list text\ngive back findings'), [])
    check("accumulate into empty list",
          run('findings as list text\nappend "a" to findings\n'
              'append "b" to findings\ngive back findings'), ["a", "b"])
    check("empty list of shape",
          run('rows as list sh.Report\nappend "x" to rows\ngive back rows'),
          ["x"])


def test_add_verb():
    print("\n=== list-grow verb `add` (was a silent no-op; strict: lists only) ===")
    check("add grows an empty list, distinct values in order",
          run('colors as list text\nadd "red" to colors\nadd "green" to colors\n'
              'add "blue" to colors\ngive back colors'),
          ["red", "green", "blue"])
    # Behavioral, per review: start with two, add a third, repeat each must SEE three (a silent
    # drop would show two). Re-add each into a second list so the count is the returned value.
    check("start with two, add a third, repeat each sees three",
          run('xs as list text\nadd "a" to xs\nadd "b" to xs\nadd "c" to xs\n'
              'seen as list text\nrepeat each x in xs\n    add x to seen\nrepeat: done\n'
              'give back seen'),
          ["a", "b", "c"])
    check("add preserves duplicates (a true append, not a set)",
          run('xs as list text\nadd "x" to xs\nadd "x" to xs\ngive back xs'),
          ["x", "x"])
    check("add and append interoperate on the same list",
          run('xs as list text\nadd "a" to xs\nappend "b" to xs\nadd "c" to xs\ngive back xs'),
          ["a", "b", "c"])
    # Strict: `add` is lists only -- a non-list target fails loud instead of silently concatenating.
    raised = False
    msg = ""
    try:
        run('note "hello"\nadd "x" to note\ngive back note')
    except Exception as e:
        raised, msg = True, str(e)
    check("add to a string fails loud", raised, True)
    check("the failure points to append/prepend for strings", "add works on lists" in msg, True)


def test_inline_populated_list():
    print("\n=== inline populated list: `NAME as list V, V, ...` ===")
    check("inline list of strings, distinct and ordered",
          run('colors as list "red", "green", "blue"\ngive back colors'),
          ["red", "green", "blue"])
    check("inline list of numbers",
          run('nums as list 1, 2, 3\ngive back nums'), [1, 2, 3])
    check("single inline value",
          run('one as list "solo"\ngive back one'), ["solo"])
    check("inline list then grows with add",
          run('colors as list "red", "green"\nadd "blue" to colors\ngive back colors'),
          ["red", "green", "blue"])
    # Disambiguation guard: a bare type word stays the EMPTY-typed form, not a one-item list.
    check("`as list text` is still an empty list, not populated with the word 'text'",
          run('errors as list text\ngive back errors'), [])


def test_create_list_block():
    print("\n=== create list block: `create list NAME / v / ... / create: done` ===")
    check("block list of strings, distinct and ordered",
          run('create list colors\n    "red"\n    "green"\n    "blue"\n'
              'create: done\ngive back colors'),
          ["red", "green", "blue"])
    check("block list of numbers",
          run('create list nums\n    1\n    2\n    3\ncreate: done\ngive back nums'),
          [1, 2, 3])
    check("block list then grows with add",
          run('create list colors\n    "red"\ncreate: done\nadd "green" to colors\n'
              'give back colors'),
          ["red", "green"])
    # Guard: `create OBJECT` (no LIST_KW) still builds an object, not a list.
    check("create Report (no LIST_KW) still builds an object",
          run('create Report\n    title "Q4"\ncreate: done\ngive back report as.json'),
          '{"title": "Q4"}')


def test_list_position():
    print("\n=== 1-based position/pos on a plain list (was silent None) ===")
    base = 'colors as list "red", "green", "blue"\n'
    check("position.1 -> first (1-based, not 0-based)", run(base + 'give back colors.position.1'), "red")
    check("position.2 -> second", run(base + 'give back colors.position.2'), "green")
    check("position.3 -> third", run(base + 'give back colors.position.3'), "blue")
    check("pos.2 shorthand -> second element", run(base + 'give back colors.pos.2'), "green")
    check("first still resolves", run(base + 'give back colors.first'), "red")
    check("last still resolves", run(base + 'give back colors.last'), "blue")


def test_bracket_literal_retired():
    print("\n=== [a,b,c] value literal retired; tag/facet brackets kept ===")
    raised = False
    msg = ""
    try:
        run('hold picks ["a", "b", "c"]\ngive back picks')
    except Exception as e:
        raised, msg = True, str(e)
    check("[a,b,c] value literal fails loud", raised, True)
    check("message points to create list / as list",
          'as list' in msg and 'create list' in msg, True)
    # class_tag [phi] is a DIFFERENT bracket use (field tag) -- must still parse after the retirement.
    ct_src = 'shape Intake\n    note as text [phi]\nshape: done\n'
    ct_ok = True
    try:
        transform(_P.parse(ct_src), ct_src)
    except Exception:
        ct_ok = False
    check("class_tag [phi] still parses (tag brackets untouched)", ct_ok, True)


if __name__ == "__main__":
    test_create_multifield_json()
    test_make_is_retired()
    test_append_prepend_list()
    test_empty_growable_list()
    test_add_verb()
    test_inline_populated_list()
    test_create_list_block()
    test_list_position()
    test_bracket_literal_retired()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
