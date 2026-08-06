# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""test_correctness_gate.py — CORRECTNESS, not just "does it run".

The old gate only checked that a construct executed without erroring, so a
function that ran but returned the wrong answer (math funcs, replace-inline)
passed as green. This gate asserts documented value-producing forms return the
RIGHT output. KNOWN_BROKEN tracks the not-yet-fixed ones so the suite stays green;
that set shrinks to empty as they're fixed, at which point every form is enforced.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)
NUMS = 'create list nums\n    10\n    20\n    30\ncreate: done\n'
def V(expr, setup=""): return f'{setup}hold r {expr}\ngive back 200 r\n'
def S(stmt, name, setup=""): return f'{setup}{stmt}\ngive back 200 {name}\n'
def CMP(f, a=8, b=5): return f'a {a}\nb {b}\ncompare a to b\ncompare: done\ngive back 200 comparison.{f}\n'

CASES = [
 ('as.uppercase', V('"hi" as.uppercase'), 'HI'),
 ('as.lowercase', V('"HI" as.lowercase'), 'hi'),
 ('trim', V('"  x  " trim'), 'x'),
 ('trim.front', V('"  x  " trim.front'), 'x  '),
 ('trim.back', V('"  x  " trim.back'), '  x'),
 ('left', V('"abcde" left 2'), 'ab'),
 ('right', V('"abcde" right 2'), 'de'),
 ('after', V('"a@b.com" after "@"'), 'b.com'),
 ('before', V('"a@b.com" before "@"'), 'a'),
 ('replace-block', 'hold s "cat"\nreplace in s\n    "c" with "b"\nreplace: done\ngive back 200 s\n', 'bat'),
 ('mask.all', V('"1234567890" mask.all except last 4'), '******7890'),
 ('truncate-words', V('"one two three" truncate.to 2 words'), 'one two'),
 ('truncate-chars', V('"abcdef" truncate.to 3 characters'), 'abc'),
 ('as.int', V('"250" as.int'), 250), ('as.number', V('"3.5" as.number'), 3.5),
 ('as.dec', V('"3.14159" as.dec'), 3.14159), ('as.dec.2', V('"3.14159" as.dec.2'), 3.14),
 ('as.uc', V('"hi" as.uc'), 'HI'), ('as.lc', V('"HI" as.lc'), 'hi'),
 ('as.title', V('"hi bo" as.title'), 'Hi Bo'), ('as.bool', V('"true" as.bool'), True),
 ('absolute', S('absolute -5 as n', 'n'), 5),
 ('sum', S('sum of nums as s', 's', NUMS), 60),
 ('average', S('average of nums as a', 'a', NUMS), 20.0),
 ('minimum', S('minimum of nums as m', 'm', NUMS), 10),
 ('maximum', S('maximum of nums as x', 'x', NUMS), 30),
 ('percentage', S('percentage 25 of 200 as p', 'p'), 12.5),
 ('add', V('(3 + 4)'), 7), ('sub', V('(10 - 3)'), 7), ('mul', V('(2 * 3)'), 6),
 ('div', V('(10 / 4)'), 2.5), ('mod', V('(10 % 3)'), 1), ('concat', V('("Hi " & "Bo")'), 'Hi Bo'),
 ('default-fallback', V('m default "fb"', 'hold m null\n'), 'fb'),
 ('default-value-present', V('"real" default "fb"'), 'real'),
 ('remove.special', V('"a@b!c" remove.special'), 'abc'),
 ('remove.html', V('"<p>hi</p>" remove.html'), 'hi'),
 ('remove.ws', V('"a b c" remove.ws'), 'abc'),
 ('pad.right', V('"5" pad.right to 3 with "0"'), '500'),
 ('pad.left', V('"5" pad.left to 3 with "0"'), '005'),
 # otherwise-coalesce specimen removed: the `display m otherwise "fb"` statement-coalesce form
 # was deleted by design (OQ-005 -- no design record; `otherwise` is a block fallback only).
 # Testing a deleted form as if it should return a value is stale; the correct behavior is that
 # it errors, which the block-form `when m is empty / give back ...` covers.
 ('then-thread', 'result (5 + 3) then (it * 2)\ngive back 200 result\n', 16),
 ('cmp.equal', CMP('equal', 5, 5), True), ('cmp.difference', CMP('difference'), 3),
 ('cmp.absolute', CMP('absolute'), 3), ('cmp.percentage', CMP('percentage'), 60.0),
 ('cmp.ratio', CMP('ratio'), 1.6), ('cmp.larger', CMP('larger'), 8), ('cmp.smaller', CMP('smaller'), 5),
 ('encode-b64', S('encode "hi" as base64 as r', 'r'), 'aGk='),
 ('decode-b64', S('decode "aGk=" from base64 as r', 'r'), 'hi'),
]
# Not yet fixed. Shrinks to empty. When empty, every form above is enforced.
KNOWN_BROKEN = set()  # empty: every documented value-producing form is enforced

def _result(src, expected):
    try:
        got = MohioInterpreter().run(transform(_P.parse(src), src)).get('body')
        return (str(got) == str(expected)), got
    except Exception as e:
        return False, str(e).splitlines()[0][:34]

def test_correctness_gate():
    regressions = []
    fixed_but_listed = []
    for label, src, exp in CASES:
        ok, got = _result(src, exp)
        if label in KNOWN_BROKEN:
            if ok:
                fixed_but_listed.append(label)   # now works -> remove from KNOWN_BROKEN
        elif not ok:
            regressions.append((label, got, exp))  # was fine, now broken -> real regression
    assert not regressions, f"CORRECTNESS REGRESSIONS: {regressions}"
    assert not fixed_but_listed, (
        f"These now pass -- remove from KNOWN_BROKEN: {fixed_but_listed}")

if __name__ == "__main__":
    b = {'CORRECT': [], 'FALSE-GREEN': [], 'NO-EXECUTOR': [], 'ERROR': []}
    for label, src, exp in CASES:
        try:
            got = MohioInterpreter().run(transform(_P.parse(src), src)).get('body')
            b['CORRECT' if str(got) == str(exp) else 'FALSE-GREEN'].append(label)
        except Exception as e:
            m = str(e).splitlines()[0]
            b['NO-EXECUTOR' if 'No executor' in m else 'ERROR'].append(label)
    print(f"CORRECT {len(b['CORRECT'])}/{len(CASES)} | "
          f"false-green {b['FALSE-GREEN']} | no-exec {b['NO-EXECUTOR']} | error {b['ERROR']}")
    # Honest exit: any false-green (ran but wrong result), no-executor, or error is a failure.
    sys.exit(1 if (b['FALSE-GREEN'] or b['NO-EXECUTOR'] or b['ERROR']) else 0)
