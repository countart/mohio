# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Sector enforcement: the compiler REFUSES THE BUILD, it does not report at runtime.

This is the difference between a compliance language and a compliance library. A library inspects
what a program did. This reads the sector directive, looks up the constraint matrix that directive
activates, checks the program's own source against it, and produces no artifact when the source
violates it. The violation never reaches a running system because there is nothing to run.

WHAT THIS TEST EXISTS FOR
Two of these constraints were implemented and enforced NOTHING, and neither failed loudly enough
to notice, because no shipped sector profile declared anything that would have exercised them:

  1. `never store` in a profile was silently dropped by the loader. Its regex ended the field's
     modifier capture at the first newline, so a modifier written the natural indented way --
     which is how anyone would write it -- was discarded. Only cramming it onto the same line
     worked. A compliance control that reads as present and enforces nothing.

  2. The shape check looked for a child whose rule name contained 'field'. Shape fields arrive
     wrapped as `shape_body -> shape_field`, so the filter matched nothing and every never-store
     field passed the compiler untouched.

Both are the same failure: a filter that does not name the thing discards it in silence. The
profile below exists to keep these paths exercised, because a mechanism nothing reaches is a
mechanism nobody can tell is broken.

The profile is a SAMPLE. It is not legally reviewed and asserts nothing about what any framework
actually requires.
"""
import os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV = dict(os.environ, PYTHONPATH=_ROOT, DATABASE_URL=':memory:',
            MOHIO_ENCRYPTION_KEY='testkey')

from mohio_sector_loader import load_sector_profile

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ── the profile parses what it declares ───────────────────────────────────────────────
prof = load_sector_profile('demo_regulated')
check("the sample regulated profile loads", prof is not None)
if prof is None:
    print("\nRESULTS: {} passed, {} failed".format(_p, _f + 1))
    sys.exit(1)

check("an indented `never store` modifier is captured (not dropped at the newline)",
      prof.is_never_store('ssn'), f"never_store={sorted(prof.never_store_fields)}")
check("a second never-store field is captured", prof.is_never_store('card_number'))
check("a field WITHOUT the modifier is not marked never-store",
      not prof.is_never_store('region'))
check("field classifications parse alongside the modifier",
      set(prof.get_field_classifications('ssn')) == {'phi', 'pii'},
      str(prof.get_field_classifications('ssn')))
check("the profile's compliance frameworks parse", 'hipaa' in (prof.compliance or []))
check("the confidence floor parses",
      abs(float(prof.get_confidence_floor('critical_decision')) - 0.90) < 1e-9)


def check_program(body):
    """Run `mio check` on a program and return (exit_code, output)."""
    fd, path = tempfile.mkstemp(suffix='.mho')
    os.write(fd, body.encode()); os.close(fd)
    try:
        r = subprocess.run([sys.executable, os.path.join(_ROOT, 'mio.py'), 'check', path],
                           env=_ENV, capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)


SECTOR = 'sector: demo_regulated\n'

# ── a never-store field REFUSES THE BUILD ─────────────────────────────────────────────
_code, _out = check_program(
    SECTOR + 'shape Patient\n    method POST\n    ssn as text\nshape: done\n'
             'give back 200 "x"\n')
check("a shape storing a never-store field is refused at compile time", _code != 0)
check("the refusal names the field and the sector",
      'ssn' in _out and 'demo_regulated' in _out)
check("the refusal says what to do instead",
      'token' in _out.lower(), _out[-200:])

# ── an ordinary field still compiles ──────────────────────────────────────────────────
_code_ok, _out_ok = check_program(
    SECTOR + 'shape Patient\n    method POST\n    region as text\nshape: done\n'
             'give back 200 "x"\n')
check("a field with no never-store classification compiles", _code_ok == 0, _out_ok[-200:])

# ── the confidence floor REFUSES THE BUILD ────────────────────────────────────────────
_DECIDE = ('amt 1\nai.decide critical_decision returns boolean\n'
           '    confidence above %s\n    weigh amt\n'
           '    not confident\n        give back false\nai.decide: done\n'
           'give back 200 "x"\n')
_code_low, _out_low = check_program(SECTOR + _DECIDE % '0.50')
check("a decision below the sector's confidence floor is refused at compile time",
      _code_low != 0)
check("the refusal names the declared value and the floor",
      '0.5' in _out_low and '0.9' in _out_low)

_code_hi, _ = check_program(SECTOR + _DECIDE % '0.95')
check("a decision at or above the floor compiles", _code_hi == 0)

# ── without the sector declared, neither constraint applies ───────────────────────────
_code_nos, _ = check_program(
    'shape Patient\n    method POST\n    ssn as text\nshape: done\ngive back 200 "x"\n')
check("with no sector declared the same program compiles (nothing is activated)",
      _code_nos == 0)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
