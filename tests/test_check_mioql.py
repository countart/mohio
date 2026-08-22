# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_check_mioql.py -- A1: check exists / check count / check unique (MioQL).

- check exists -> boolean. on.success fires when found; on.failure is now RESERVED for a
  genuine driver error (T1-GUARD-FAILOPEN Part B, 2026-08-19 -- supersedes the old "on.failure
  when not found" pattern this file used to lock). A real not-found is a legitimate answer,
  not a failure: on.success does not fire either (there's nothing to acknowledge succeeding),
  and when/otherwise is the correct channel to branch on found vs not-found -- same shape
  check unique already had (below), now consistent across both.
- check count  -> integer bound to `as NAME`; fail loud without `as`.
- check unique -> boolean. on.success / on.failure are operational (did the
  query run); the answer branches on when-empty (available, count 0) /
  otherwise (taken, count > 0) -- T1-CHECK-UNIQUE-REDESIGN, 2026-08-11.
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
check("exists not found -> on.failure does NOT fire; on.success DOES (STATE channel: the "
      "query ran fine, superseded 'on.failure=not-found', RUN-1/Part-B consistent)",
      run('check exists f in db.users match email to "missing@x.com"\n    on.success\n        show "YES"\n    on.failure\n        show "NO"\ncheck: done\n') == ["YES"])
check("exists not found -> when-empty/otherwise IS the correct channel now",
      run('check exists f in db.users match email to "missing@x.com"\n'
          '    when f is true\n        show "YES"\n    otherwise\n        show "NO"\ncheck: done\n') == ["NO"])
check("exists found -> when/otherwise still works (regression guard)",
      run('check exists f in db.users match email to "taken@x.com"\n'
          '    when f is true\n        show "YES"\n    otherwise\n        show "NO"\ncheck: done\n') == ["YES"])
check("count as NAME binds the integer",
      run('check count as total in db.users\n    on.success\n        show total\ncheck: done\n') == ["1"])
check("unique taken -> otherwise (already exists)",
      run('check unique in db.users match email to "taken@x.com"\n    when empty\n        show "AVAIL"\n    otherwise\n        show "TAKEN"\ncheck: done\n') == ["TAKEN"])
check("unique available -> when empty",
      run('check unique in db.users match email to "new@x.com"\n    when empty\n        show "AVAIL"\n    otherwise\n        show "TAKEN"\ncheck: done\n') == ["AVAIL"])

# count without `as` fails loud (no magic default)
try:
    run('check count in db.users\n    on.success\n        show "c"\ncheck: done\n')
    check("count without as fails loud", False)
except Exception as e:
    check("count without as fails loud", 'as NAME' in str(e))

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
