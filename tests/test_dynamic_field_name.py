# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Dynamic field-name writes (`colname to 42`) actually work (2026-08-04).

The grammar documents a dynamic save-field form -- `save_field: dotted_name _TO value_expr`,
its own comment citing `puzzle.flag_set to "true"` -- where the COLUMN NAME is resolved at
runtime. It never worked, and failed silently in the worst way: it wrote a column literally
named '' holding the field-NAME text, and discarded the developer's actual value, with no error
at any layer (parse clean, `mio check` clean, write "succeeded").

ROOT CAUSE (four dead layers, all in the transformer -- NOT an Earley ambiguity):
  1. `save_field` discriminated the static/dynamic forms by looking for a `to` token. `_TO` is
     underscore-prefixed, which tells Lark to DISCARD it, so that token is structurally
     guaranteed absent -- the check was always False and every dynamic field fell into the
     static branch (name='' from a missing NAME token; value slot grabbed the field-name node).
     Now discriminates on STRUCTURE: static -> [Token(NAME), ...], dynamic -> [DottedName, ...].
  2-4. `save_block`, `save_or_update_block` and `update_block` each collected only `FieldValue`
     when building their field lists, so even a correct DynamicFieldValue was dropped on the
     floor -- which is what made every DynamicFieldValue branch in the interpreter unreachable.

Also fixed here, because fixing 1-4 makes these paths live for the first time:
  - `_exec_SaveBlock` had NO DynamicFieldValue branch at all (would silently skip the field).
  - The field NAME was unguarded in every executor (only the VALUE had the A3.1 guard), so an
    undefined or empty name wrote a column literally named '' / 'None'.

Run: `python tests/test_dynamic_field_name.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark, Token
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import FieldValue, DynamicFieldValue

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def run(src):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    it.shown = []
    try:
        r = it.run(prog)
    except Exception as e:                      # A3.1 guards raise MohioRuntimeError
        return 'FAILLOUD: ' + str(e)
    if isinstance(r, dict) and r.get('status') == 500:
        return 'FAILLOUD: ' + str(r.get('body'))
    return it.shown

CONN = 'connect db as sqlite from env.DATABASE_URL\n'

# ── AST: the dynamic form produces DynamicFieldValue with BOTH parts intact ────────────────
def fields_of(src):
    return getattr(transform(_P.parse(src), src).statements[-1], 'fields', [])

dyn = fields_of(CONN + 'colname "amount"\nsave to db.t\n    colname to 42\nsave: done\n')
check("dynamic form builds a DynamicFieldValue (was a FieldValue with name='')",
      len(dyn) == 1 and isinstance(dyn[0], DynamicFieldValue), str(dyn))
check("dynamic form keeps the field-name node AND the real value (value was discarded)",
      len(dyn) == 1 and isinstance(dyn[0], DynamicFieldValue)
      and getattr(dyn[0].field_name, 'parts', None) == ['colname']
      and getattr(dyn[0].value, 'value', None) == 42, str(dyn))

stat = fields_of(CONN + 'save to db.t\n    amount 5\nsave: done\n')
check("static form still builds a plain FieldValue (regression guard)",
      len(stat) == 1 and isinstance(stat[0], FieldValue) and stat[0].name == 'amount', str(stat))

# ── Runtime: the value lands in the named column, on all three write verbs ─────────────────
check("save: dynamic field writes the real value into the resolved column",
      run(CONN + 'colname "amount"\nsave to db.t\n    colname to 42\nsave: done\n'
          'find x in db.t\nfind: done\nshow "amount={{ x.first.amount }}"\n') == ['amount=42'])

check("upsert: dynamic field writes the real value into the resolved column",
      run(CONN + 'colname "score"\nupsert db.t\n    match id to "1"\n    colname to 99\nupsert: done\n'
          'find x in db.t\nfind: done\nshow "score={{ x.first.score }}"\n') == ['score=99'])

check("update: dynamic field writes the real value into the resolved column",
      run(CONN + 'colname "score"\nsave to db.t\n    id "1"\n    score 1\nsave: done\n'
          'update db.t\n    match id to "1"\n    colname to 77\nupdate: done\n'
          'find x in db.t\nfind: done\nshow "score={{ x.first.score }}"\n') == ['score=77'])

check("static save unaffected (regression guard)",
      run(CONN + 'save to db.t\n    amount 5\nsave: done\n'
          'find x in db.t\nfind: done\nshow "amount={{ x.first.amount }}"\n') == ['amount=5'])

# ── The written column is the RESOLVED name, never the variable's own name ─────────────────
r = run(CONN + 'colname "amount"\nsave to db.t\n    colname to 42\nsave: done\n'
        'find x in db.t\nfind: done\nshow "row={{ x.first }}"\n')
check("the column is named by the RESOLVED value ('amount'), not '' or 'colname'",
      isinstance(r, list) and "'amount': 42" in r[0] and "''" not in r[0], str(r))

# ── Field NAME is guarded like the value: undefined or empty fails loud, never a junk column ─
for verb_label, src in [
    ("save",          CONN + 'save to db.t\n    nosuchvar to 42\nsave: done\n'),
    ("upsert",        CONN + 'upsert db.t\n    match id to "1"\n    nosuchvar to 42\nupsert: done\n'),
]:
    out = run(src)
    check(f"{verb_label}: an UNDEFINED dynamic field name fails loud",
          isinstance(out, str) and out.startswith('FAILLOUD') and 'nosuchvar' in out, str(out))

for verb_label, src in [
    ("save",   CONN + 'colname ""\nsave to db.t\n    colname to 42\nsave: done\n'),
    ("update", CONN + 'colname ""\nsave to db.t\n    id "1"\n    score 1\nsave: done\n'
                      'update db.t\n    match id to "1"\n    colname to 5\nupdate: done\n'),
]:
    out = run(src)
    check(f"{verb_label}: an EMPTY dynamic field name fails loud (no '' column)",
          isinstance(out, str) and out.startswith('FAILLOUD') and 'empty' in out.lower(), str(out))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
