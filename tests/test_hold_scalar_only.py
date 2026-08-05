# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`hold` is scalar-only: both block forms are retired and fail loud (B6, 2026-08-01).

`hold` freezes ONE scalar value and nothing else. Two block forms used to be fully wired
(grammar productions + transformer + _exec_HoldDecl all ran them) and were never retired in
code, despite the ratified scalar-only decision:

  * LIST form    -- `hold name / "a" / "b" / hold: done`
  * PROFILE form -- `hold name / field value / field value / hold: done`

Worse than merely non-canonical: the profile form could SILENTLY DROP its last field depending
on the statements around it (Earley ambiguity), and `mio check` reported no error. Both are now
retired in the transformer and fail loud, pointing at the `create` replacement. This test locks:
both block forms fail loud with a clear pointer; scalar `hold` is untouched; `create` builds the
same values reliably in any program position.

Run: `python tests/test_hold_scalar_only.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_RAW = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def compile_err(src):
    """Return the error string if compiling src fails, else None."""
    try:
        transform(_P.parse(src), src)
        return None
    except Exception as e:
        return str(e)


def run_show(src):
    """Run src and return the last shown value (str), or the error string."""
    try:
        it = MohioInterpreter()
        it.run(transform(_P.parse(src), src))
        return it.shown[-1] if getattr(it, 'shown', None) else None
    except Exception as e:
        return f"<error: {e}>"


# ── 1. the LIST block form is retired and fails loud, pointing at `create list` ──────────
for _label, _src in (
    ("list form, with closer",   'hold xs\n    "a"\n    "b"\nhold: done\nshow xs\n'),
    ("list form, first stmt",    'hold xs\n    "a"\n    "b"\nhold: done\n'),
    ("list form, preceded",      'hold z "9"\nhold xs\n    "a"\n    "b"\nhold: done\n'),
):
    _e = compile_err(_src)
    check(f"LIST {_label}: fails loud", _e is not None, _src)
    check(f"LIST {_label}: names `create list`", bool(_e) and 'create list' in _e, _e or '')

# ── 2. the PROFILE/dict block form is retired and fails loud, pointing at `create` ──────
#    Every position: first statement, and preceded (the silent-field-drop trigger). Both must
#    now fail loud rather than silently drop the last field.
for _label, _src in (
    ("profile form, first stmt", 'hold rec\n    a "1"\n    b "2"\nhold: done\n'),
    ("profile form, preceded",   'hold z "9"\nhold rec\n    a "1"\n    b "2"\n'),
    ("profile form, no closer",  'hold z "9"\nhold rec\n    a "1"\n    b "2"\nshow rec\n'),
):
    _e = compile_err(_src)
    check(f"PROFILE {_label}: fails loud", _e is not None, _src)
    check(f"PROFILE {_label}: names `create`", bool(_e) and 'create' in (_e or ''), _e or '')

# ── 3. scalar `hold x value` is UNAFFECTED (the whole point of the retirement) ──────────
check("scalar hold binds a text value", run_show('hold x "hello"\nshow x\n') == 'hello')
check("scalar hold binds a number", str(run_show('hold pi 3.14\nshow pi\n')) == '3.14')
check("scalar hold default fires on missing source",
      run_show('hold y missing default "fb"\nshow y\n') == 'fb')
check("scalar hold then release then restate",
      run_show('hold n "A"\nrelease n\nn "B"\nshow n\n') == 'B')

# ── 4. the `create` replacements BUILD the same values, reliably, in ANY position ──────
#    (the profile form's bug was position-dependent field loss; create must not have it)
check("create list builds a real list, preceded",
      run_show('hold z "9"\ncreate list xs\n    "a"\n    "b"\n    "c"\ncreate: done\nshow xs')
      == ['a', 'b', 'c'])
check("create block keeps ALL fields, first stmt",
      run_show('create rec\n    a "1"\n    b "2"\n    c "3"\ncreate: done\nshow rec')
      == {'a': '1', 'b': '2', 'c': '3'})
check("create block keeps ALL fields, preceded (no silent drop)",
      run_show('hold z "9"\ncreate rec\n    a "1"\n    b "2"\ncreate: done\nshow rec')
      == {'a': '1', 'b': '2'})
check("create block field is addressable",
      run_show('hold z "9"\ncreate rec\n    a "1"\n    b "2"\ncreate: done\nshow rec.b') == '2')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
