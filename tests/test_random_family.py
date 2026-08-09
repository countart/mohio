#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Lock tests for the random / selection family.

Covers the bugs found while wiring `random from <source>`:
  1. The `create list` builder must produce a real list, and the RETIRED `hold`
     list-block form (`hold name / items / hold: done`, B6) must fail loud pointing
     at `create list` -- three chained bugs previously made the old form store an
     empty dict / None, which is exactly why it is retired.
  2. `random from <create list>` must pick a varied element (not always None).
  3. `random.N` (no `from`) must still return an integer 1..N (no collision with
     the killed "N items" meaning).
  4. `random from <non-collection>` must fail loud with the corrected hint
     (no `[...]` list-literal advice — canon forbids data-list literals).

DB-table and find-result source kinds are exercised in the integration suite
(they need the seeded-db scaffold); here we lock the held-list + fail-loud paths
that need no database.
"""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
# This is NOT an auth test. It used to gate its handler behind `require role "player"` and pass a
# trusted client `_roles` payload (via MOHIO_TRUST_PROXY_ROLES=1) to reach the random code. Auth
# rebuild Item 1 (2026-08-02) removed the client-roles path entirely -- roles are established
# server-side by `grant role`. Rather than re-couple this random test to the auth mechanism, the
# handler no longer gates on a role at all; the random paths are what is under test.

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MockAiRuntime
from mohio_ast import HoldDecl, RandomValue, Assignment, ListLiteral

_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_HANDLER = '''shape Cmd
    command as text
shape: done
listen for
    new sh.Cmd
%BODY%
    new: done
listen: done
'''


def _parse(body):
    return transform(_P.parse(_HANDLER.replace('%BODY%', body)), _HANDLER)


def _run(body):
    prog = _parse(body)
    it = MohioInterpreter(ai=MockAiRuntime(), verbose=False)
    it.run_declarations(prog)
    r = it.run(prog, request={'command': 'x'})
    b = r.get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


def _find_nodes(prog, cls):
    found = []
    def walk(n):
        if isinstance(n, cls):
            found.append(n)
        for a in getattr(n, '__dict__', {}).values():
            if isinstance(a, list):
                for x in a:
                    walk(x)
            else:
                walk(a)
    walk(prog)
    return found


PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}")


# 1a. The RETIRED hold list-block form fails loud, pointing at `create list` (B6).
retired = ('        hold rooms\n'
           '            "troll_room"\n'
           '            "round_room"\n'
           '        hold: done\n'
           '        give back 200 rooms')
_retired_msg = ''
try:
    _parse(retired)
except Exception as e:
    _retired_msg = str(e)
check("retired hold list-block fails loud", bool(_retired_msg))
check("retired hold list-block points at `create list`", 'create list' in _retired_msg)

# 1b. The `create list` builder produces a real list node (Assignment -> ListLiteral).
body = ('        create list rooms\n'
        '            "troll_room"\n'
        '            "round_room"\n'
        '            "cellar"\n'
        '        create: done\n'
        '        give back 200 rooms')
prog = _parse(body)
rooms = next((a for a in _find_nodes(prog, Assignment)
              if getattr(a, 'name', None) == 'rooms'), None)
check("create list: builds a list Assignment", rooms is not None and rooms.type_name == 'list')
check("create list: items captured as a ListLiteral",
      rooms is not None and isinstance(rooms.value, ListLiteral)
      and len(rooms.value.items) == 3)

# 2. random from <create list> -> a member of the list, and it varies
body = ('        create list rooms\n'
        '            "troll_room"\n'
        '            "round_room"\n'
        '            "cellar"\n'
        '        create: done\n'
        '        pick random from rooms\n'
        '        give back 200 pick')
prog = _parse(body)
rv = _find_nodes(prog, RandomValue)
check("random from: parses as select with a source",
      any(r.kind == 'select' and r.source is not None for r in rv))
seen = collections.Counter()
for _ in range(30):
    seen[_run(body)] += 1
members = {'troll_room', 'round_room', 'cellar'}
check("random from held list: only ever returns list members",
      set(seen) <= members)
check("random from held list: actually varies (>1 distinct over 30 runs)",
      len(seen) > 1)

# 3. random.N (no `from`) still returns an integer 1..N
body = ('        n random.3\n'
        '        give back 200 n')
vals = {_run(body) for _ in range(30)}
check("random.3 returns integers in 1..3 (no N-items collision)",
      vals and all(isinstance(v, int) and 1 <= v <= 3 for v in vals))

# 4. random from <non-collection> fails loud with the corrected hint
body = ('        score 5\n'
        '        bad random from score\n'
        '        give back 200 bad')
out = str(_run(body))
check("random from non-collection: fails loud", 'random_source_not_a_list' in out
      or 'must be a collection' in out)
# the corrected hint must NOT teach forbidden [...] list literals
check("random error hint: no forbidden [\"a\",\"b\"] list-literal advice",
      '["a"' not in out and "['a'" not in out)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
