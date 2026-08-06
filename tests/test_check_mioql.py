# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_check_mioql.py -- A1: check exists / check count / check unique (MioQL).

- check exists -> boolean; on.success when found, on.failure when not.
- check count  -> integer bound to `as NAME`; fail loud without `as`.
- check unique -> boolean, SIGNUP polarity: on.success = available (count 0),
  on.failure = already exists (count > 0).
Non-retrieving: they answer a question, never return rows.
"""
import os
os.environ['DATABASE_URL'] = ':memory:'
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

import mohio_data
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_H = 'connect db as sqlite from env.DATABASE_URL\n'
_SEED = 'save to db.users\n    email "taken@x.com"\nsave: done\n'

def run(body):
    it = MohioInterpreter()
    it.run(transform(_P.parse(_H + _SEED + body), _H + _SEED + body), {})
    return [str(x) for x in it.shown]

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL: {name}")

check("exists found -> on.success",
      run('check exists f in db.users match email to "taken@x.com"\n    on.success\n        show "YES"\n    on.failure\n        show "NO"\ncheck: done\n') == ["YES"])
check("exists not found -> on.failure",
      run('check exists f in db.users match email to "missing@x.com"\n    on.success\n        show "YES"\n    on.failure\n        show "NO"\ncheck: done\n') == ["NO"])
check("count as NAME binds the integer",
      run('check count as total in db.users\n    on.success\n        show total\ncheck: done\n') == ["1"])
check("unique taken -> on.failure (already exists)",
      run('check unique in db.users match.unique email to "taken@x.com"\n    on.success\n        show "AVAIL"\n    on.failure\n        show "TAKEN"\ncheck: done\n') == ["TAKEN"])
check("unique available -> on.success",
      run('check unique in db.users match.unique email to "new@x.com"\n    on.success\n        show "AVAIL"\n    on.failure\n        show "TAKEN"\ncheck: done\n') == ["AVAIL"])

# count without `as` fails loud (no magic default)
try:
    run('check count in db.users\n    on.success\n        show "c"\ncheck: done\n')
    check("count without as fails loud", False)
except Exception as e:
    check("count without as fails loud", 'as NAME' in str(e))

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
