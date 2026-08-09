# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""PCI class behaviors (standalone).

1. never-store: a save that persists a field declared `never store` (e.g. a card CVV)
   is caught at COMPILE TIME by mio's check pass, before the program ever runs.
2. [pci] fields encrypt at rest (like sec.encrypt) and stay full for authorized use.

Masking to last-4 on output is a separate, in-progress build (needs value-provenance so a
card is masked on display but full for use) and is not covered here yet.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mio

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)


def _run(src):
    return MohioInterpreter().run(transform(_P.parse(src), src)).get('body')

def _shown(src):
    i = MohioInterpreter(); i.run(transform(_P.parse(src), src)); return i.shown


def _val(b):
    return b.to_python() if hasattr(b, 'to_python') else b


def test_never_store_caught_at_compile_time():
    CARD = 'shape Card\n    cvv as text never store\n    number as text\nshape: done\n'
    # saving the cvv -> a compile error
    errs = mio._check_never_store(transform(_P.parse(
        CARD + 'save to db.cards\n    cvv "123"\n    number "4111"\nsave: done\n'), 'x'))
    assert errs and 'never store' in errs[0][0], f"expected never-store error, got {errs!r}"
    # saving without the cvv -> clean
    errs = mio._check_never_store(transform(_P.parse(
        CARD + 'save to db.cards\n    number "4111"\nsave: done\n'), 'x'))
    assert not errs, f"expected clean, got {errs!r}"


def test_pci_field_encrypts_masks_and_is_full_for_use():
    os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
    S = 'shape Card\n    number as text [pci]\nshape: done\n'
    DB = 'connect db as sqlite from env.DATABASE_URL\n'
    SAVE = 'save to db.cards\n    number "4111111111111234"\nsave: done\n'
    # masked to last-4 on give-back (display)
    masked = _val(_run(S + DB + SAVE +
                       'find rows in db.cards\nfind: done\ngive back 200 rows.first.number\n'))
    # encrypted at rest (SQL alias avoids the [pci] field-name mask so we see the raw storage)
    at_rest = _val(_run(S + DB + SAVE +
                        'retrieve raw from db.cards\n    sql\n        SELECT number as stored FROM cards\n    sql: done\nretrieve: done\ngive back 200 raw.first.stored\n'))
    # taint (option B): a [pci] value woven into a shown/returned string is masked too, so a
    # PAN cannot leak by concatenation. The value stays full at rest / for internal use; to use
    # the full number, pass the RAW value to a use channel, never a built display string.
    derived = _val(_run(S + DB + SAVE +
                     'find rows in db.cards\nfind: done\nhold c rows.first.number\ngive back 200 ("x" & c)\n'))
    assert str(masked) == '****1234', f"[pci] not masked on display: {masked!r}"
    assert isinstance(at_rest, str) and at_rest.startswith('enc:v1:'), f"[pci] not sealed at rest: {at_rest!r}"
    assert str(derived) == '****1234', f"[pci] derived value not masked on display (taint B): {derived!r}"


def test_pci_concat_masked_on_show():
    # The leak was via show: a PAN woven into text must be masked on show too (taint option B).
    os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'
    S = 'shape Card\n    ref as text\n    number as text [pci]\nshape: done\n'
    DB = 'connect db as sqlite from env.DATABASE_URL\n'
    SAVE = 'save to db.cards\n    ref "R1"\n    number "4111111111111234"\nsave: done\n'
    RET = 'retrieve c from db.cards\n    match ref to "R1"\nretrieve: done\n'
    out = _shown(S + DB + SAVE + RET + 'show ("Card: " & c.number)\n')
    assert out and '4111111111111234' not in out[-1], f"PAN leaked via concat on show: {out!r}"
    assert out[-1].strip().endswith('1234'), out[-1]


if __name__ == '__main__':
    passed = 0
    for fn, label in ((test_never_store_caught_at_compile_time, "never-store caught at compile time"),
                      (test_pci_field_encrypts_masks_and_is_full_for_use, "[pci] encrypts + masks + full for use"),
                      (test_pci_concat_masked_on_show, "[pci] concat masked on show (taint B)")):
        try:
            fn(); print(f"  [PASS] {label}"); passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {label}: {e}")
        except Exception as e:
            # A compile/runtime error must report as a failure, not crash the runner (which would
            # mask every later test in this file).
            print(f"  [FAIL] {label}: {type(e).__name__}: {str(e).splitlines()[0][:80]}")
    print(f"\nRESULTS: {passed}/3 passed")
    import sys; sys.exit(0 if passed == 3 else 1)
