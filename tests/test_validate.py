#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for `validate` + `miovalidate` (input validation).

`miovalidate <name>` declares a reusable rule set; `validate using <name>`
applies it to the inbound request (or a named source), collects `errors`, and
fires on.failure / on.success. Key guarantees verified here:

  1. Valid input passes (on.success, no errors).
  2. Type checks catch bad values (email) with clear messages.
  3. `length` and `between` modifiers are enforced.
  4. Missing `required` fields error; missing `optional` fields do not.
  5. Validation failure with NO on.failure handler fails loud (unvalidated data
     never silently passes).
  6. Rules that cannot be honestly enforced yet (`unique`, `scheme`) fail loud
     rather than giving false assurance.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, MohioValue, _Raise

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

RULES = ('miovalidate signup\n'
         '    check email as email required\n'
         '    check password as text length 8 to 64 required\n'
         '    check age as integer between 18 and 120 optional\n'
         'miovalidate: done\n')
it = MohioInterpreter()
it._exec(transform(P.parse(RULES), RULES).statements[0], Context())
check("miovalidate registers the rule set", 'signup' in it._validation_rules)

VAL = ('validate using signup\n'
       '    on.failure show "INVALID"\n'
       '    on.success show "OK"\n'
       'validate: done\n')
vnode = transform(P.parse(VAL), VAL).statements[0]

def run(data):
    ctx = Context(); ctx.set('request', MohioValue(data, 'shape')); it.shown = []
    it._exec(vnode, ctx)
    errs = ctx.get('errors'); errs = errs.to_python() if isinstance(errs, MohioValue) else errs
    return it.shown, [e['message'] for e in (errs or [])]

branch, errs = run({'email': 'a@b.com', 'password': 'longenough1', 'age': '30'})
check("valid input -> on.success, no errors", branch == ['OK'] and not errs)

branch, errs = run({'email': 'nope', 'password': 'short', 'age': '30'})
check("bad email + short password -> on.failure with 2 errors", branch == ['INVALID'] and len(errs) == 2)
check("email type error message is clear", any('valid email' in m for m in errs))
check("length error message is clear", any('8 to 64 characters' in m for m in errs))

branch, errs = run({'email': 'a@b.com', 'password': 'longenough1', 'age': '5'})
check("between enforced (age out of range)", branch == ['INVALID'] and any('between 18 and 120' in m for m in errs))

branch, errs = run({'age': '30'})
check("missing required fields error", branch == ['INVALID'] and len(errs) == 2)

branch, errs = run({'email': 'a@b.com', 'password': 'longenough1'})
check("missing optional field is fine", branch == ['OK'] and not errs)

# no on.failure handler -> must fail loud
bare = transform(P.parse('validate using signup\nvalidate: done\n'), 'b').statements[0]
loud = False
try:
    ctx = Context(); ctx.set('request', MohioValue({'email': 'bad'}, 'shape'))
    it._exec(bare, ctx)
except _Raise as e:
    loud = e.error_name == 'validation_failed'
check("failure with no on.failure handler fails loud", loud)

# unsupported rule -> fail loud, never silent pass
it._exec(transform(P.parse('miovalidate u\n    check name as text unique in db.members\nmiovalidate: done\n'), 'u').statements[0], Context())
unsupported_loud = False
try:
    ctx = Context(); ctx.set('request', MohioValue({'name': 'x'}, 'shape'))
    it._exec(transform(P.parse('validate using u\nvalidate: done\n'), 'v').statements[0], ctx)
except _Raise as e:
    unsupported_loud = e.error_name == 'validation_rule_unsupported'
check("unsupported rule (unique) fails loud", unsupported_loud)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
