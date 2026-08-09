# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_give_back_render.py — Locks the `give back render` / `give back show` fail-loud.

`give back` returns DATA; `render` / `show` are page-output blocks. Written together
(`give back render ... render: done`) the parser silently decomposes them into a junk
`give = back` assignment plus a standalone block — both individually valid, so nothing
complained. This was a classic composition bug: parse-OK but the wrong thing.

The decision (Ronnie): the trailing `render` block alone IS the page form; `give back`
is for data. `give back render`/`give back show` must FAIL LOUD with a steer.

Detection lives in MohioValidator._scan_source (same source scan as HARDCODED_CREDENTIAL
and the space-form cast), so it fires regardless of how the misparse lands.

Run: python3 tests/test_give_back_render.py   (from the compiler root)
"""
import sys, os
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer import validate

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_H = ('connect db as sqlite\n    from env.DATABASE_URL\nconnect: done\n\n'
      'shape Home\n    method GET\nshape: done\n\n')
_E = '    request for sh.Home at /home\n'


def _errs(body):
    src = _H + 'listen for\n' + body + 'listen: done\n'
    try:
        tree = _P.parse(src)
    except Exception as e:  # a parse failure is not what we are testing here
        return ['PARSE-FAIL: ' + str(e)[:60]]
    ctx = validate(tree, source=src, filename='<test>')
    return [str(e) for e in (ctx.errors or [])]


def _fails_with(body, needle):
    return any(needle in e for e in _errs(body))


_passed = 0
_failed = 0


def check(label, got, want=True):
    global _passed, _failed
    ok = (got == want)
    print(f"  [{'ok' if ok else 'XX'}] {label}")
    if ok:
        _passed += 1
    else:
        _failed += 1
        print(f"       expected {want}, got {got}")


print("=== give back render / show must fail loud ===")
check("`give back render` fails loud",
      _fails_with(_E + '        give back render\n            <h1>hi</h1>\n        render: done\n    request: done\n',
                  'give back render'))
check("`give back show` fails loud",
      _fails_with(_E + '        give back show\n            hello\n        show: done\n    request: done\n',
                  'give back show'))
check("`give back 200 render` (with status) fails loud",
      _fails_with(_E + '        give back 200 render\n            <h1>hi</h1>\n        render: done\n    request: done\n',
                  'give back render'))

print("\n=== the correct forms stay clean (no false positives) ===")
check("trailing `render` block alone -> clean",
      _errs(_E + '        render\n            <h1>hi</h1>\n        render: done\n    request: done\n') == [])
check("`give back 201 \"ok\"` -> clean",
      _errs(_E + '        give back 201 "ok"\n    request: done\n') == [])
check("`give back result_render` (variable, \\b guard) -> clean",
      _errs(_E + '        give back result_render\n    request: done\n') == [])

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
