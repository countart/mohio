# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T0-6, NARROWED by T1-QUERY-HELD (2026-08-09): find / retrieve / grab over a held (non-DB)
source must refuse loudly and clearly, never silently -- for the shapes that still refuse.

WHAT CHANGED: T0-6 originally made this refusal UNCONDITIONAL for any held source. T1-QUERY-HELD
made it CONDITIONAL: a held source that is a LIST now routes to `_filter_held_list` instead (real
filtering, proven in `tests/test_held_list_mixed_data_ruling.py`, real .mho path). This file's
original "held list refuses" assertions were about `colors as list "red", "blue", "green"` -- that
is exactly the shape that no longer refuses. Those assertions are UPDATED below to match, not
removed -- the historical narrative (THE BUG, below) is unchanged and still true for every held
source that ISN'T a list. What this file now guarantees: a non-list held source (a scalar, file
content) still refuses exactly as T0-6 built it.

THE BUG (found by this session's MioQL survey): `_resolve_source` (mohio_interpreter.py) maps
EVERY source -- a real `db.table`, or a bare NAME that happens to be a held list/variable -- into
a table-name string, with no branch that tells the two apart. `_db_or_fail` runs first and, with
no database connected, raises `no_db_connection` -- true of the process, but the wrong diagnosis:
a database would not have made `find X in <heldList>` work either. Worse, WITH a database
connected, `_resolve_source` happily resolves the held variable's name to a phantom table that was
never created, the query runs against it, and the result comes back silently unbound -- no error,
no warning, `mio check` clean -- which is exactly the failure mode CLAUDE.md calls out as the
worst kind: it looks finished and the gap surfaces later somewhere expensive.

THE FIX (T0-6): `_refuse_held_source_query` (mohio_interpreter.py, next to `_resolve_source`) runs
at the top of `_exec_FindBlock` / `_exec_RetrieveBlock` / `_exec_GrabBlock`, before `_db_or_fail`
or `_resolve_source` ever see the source. If the source is a single-part DottedName (a bare NAME --
what a held-variable source always parses to; see source_ref's transformer) AND that name
currently resolves to a real held variable (`ctx.exists`), it refuses immediately with a message
that names the real problem, points at what already works (`repeat each`, `random from`), and
does not blame database connectivity.

THE NARROWING (T1-QUERY-HELD): `_resolve_held_list_source` now runs FIRST, ahead of
`_refuse_held_source_query`. If the held value is a list, that function is never reached at all --
`_filter_held_list`'s ruling handles it. `_refuse_held_source_query` still runs, and still
refuses, for every other held-source shape.

RUNTIME-ONLY, DELIBERATELY: whether a given name is a held variable at this exact point in a
program is a live question about the scope chain (conditionals, loops, task-local and session
scope) that only `ctx.exists()` can answer soundly. A static per-file "was this name assigned
anywhere" scan (the shape of mohio_reachability.py's scan_undeclared_connectors, the closest
existing analog) would risk the opposite bug: refusing a legitimate `find X in db.colors` because
an unrelated held variable named `colors` exists elsewhere in the same file. `mio check` does NOT
catch this -- locked in below as an explicit, intentional fact, not an oversight.

Does NOT wire querying a held source (that is a separate Tier 1 unit). Does NOT touch
_resolve_source's or _db_or_fail's existing DB behavior.

Run: `python tests/test_query_held_source_failloud.py`.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def run_src(src):
    it = MohioInterpreter()
    return it.run(transform(P.parse(src), src)), it


def run_src_error(src):
    """REAL path, expect a raised MohioRuntimeError. Returns (True, message) or (False, why-not)."""
    try:
        run_src(src)
        return False, "no exception raised"
    except MohioRuntimeError as e:
        return True, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def refuses_as_held_source(src, verb, name='colors'):
    """The verb refused with the NEW held-source message, not any other error."""
    try:
        run_src(src)
        return False, "no exception raised"
    except MohioRuntimeError as e:
        msg = str(e)
        ok = (f"`{verb}` cannot query" in msg and "held value" in msg
              and "not built yet in this release" in msg
              and "repeat each" in msg and "random from" in msg
              and "no_db_connection" not in msg
              and "database connection" not in msg)
        return ok, msg
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


CONNECT = 'connect db as sqlite from env.DATABASE_URL\n'
HELD    = 'colors as list "red", "blue", "green"\n'


print("=== find / retrieve / grab over a held LIST: NO db connected -- NOW FILTERS (T1-QUERY-HELD) ===")
print("    (STALE as of T1-QUERY-HELD, updated in place -- see the module docstring's WHAT CHANGED)")

res, it = run_src(HELD + 'find matches in colors\nfind: done\nshow matches\n')
check("find: a held LIST with no WHERE now returns the list itself, not a refusal",
      it.shown == [['red', 'blue', 'green']], it.shown)

# `match item to "blue"` names the field "item", not the special `it` self-reference a
# scalar-only list needs (case 2 of test_held_list_mixed_data_ruling.py) -- so this specific
# program still fails loud, just with the NEW, more precise message, not the old held-source one.
ok, detail = run_src_error(
    HELD + 'retrieve item from colors\n    match item to "blue"\nretrieve: done\nshow item\n')
check("retrieve: a field-named match against a scalar list still fails loud, with the NEW message",
      ok and "holds plain values, not records" in detail, detail)

res, it = run_src(HELD + 'grab item from colors\ngrab: done\nshow item\n')
check("grab: no match clause at all binds None, same rule as the db path (unrelated to T1-QUERY-HELD)",
      it.shown == [None], it.shown)


print("\n=== find / retrieve / grab over a held LIST: WITH a db connected -- same new behavior ===")
print("    (proves the routing happens before _db_or_fail, db connection state is irrelevant)")

res, it = run_src(CONNECT + HELD + 'find matches in colors\nfind: done\nshow matches\n')
check("find: identical result with a db connected -- the held-list route never reaches _db_or_fail",
      it.shown == [['red', 'blue', 'green']], it.shown)

ok, detail = run_src_error(
    CONNECT + HELD +
    'retrieve item from colors\n    match item to "blue"\nretrieve: done\nshow item\n')
check("retrieve: same new fail-loud message with a db connected",
      ok and "holds plain values, not records" in detail, detail)

res, it = run_src(CONNECT + HELD + 'grab item from colors\ngrab: done\nshow item\n')
check("grab: same None-with-no-match result with a db connected",
      it.shown == [None], it.shown)


print("\n=== find / retrieve / grab over a held SCALAR (non-list): T0-6's refusal STILL applies ===")
print("    (the part of T0-6 this file never actually covered before -- it only ever tested lists)")

HELD_SCALAR = 'hold total 5\n'

ok, detail = refuses_as_held_source(
    HELD_SCALAR + 'find x in total\nfind: done\nshow x\n', 'find', name='total')
check("find over a held SCALAR still refuses with the T0-6 held-source message", ok, detail)

ok, detail = refuses_as_held_source(
    HELD_SCALAR + 'retrieve x from total\n    match id to "1"\nretrieve: done\nshow x\n',
    'retrieve', name='total')
check("retrieve over a held SCALAR still refuses with the T0-6 held-source message", ok, detail)

ok, detail = refuses_as_held_source(
    HELD_SCALAR + 'grab x from total\n    match id to "1"\ngrab: done\nshow x\n', 'grab', name='total')
check("grab over a held SCALAR still refuses with the T0-6 held-source message", ok, detail)


print("\n=== find / retrieve / grab over a REAL db table: unchanged ===")

DB_SEED = (CONNECT +
           'save to db.items\n    id "1"\n    name "Aria"\nsave: done\n'
           'save to db.items\n    id "2"\n    name "Bo"\nsave: done\n')

res, _ = run_src(DB_SEED + 'find rows in db.items\nfind: done\ngive back 200 rows\n')
_body = res.get('body') if isinstance(res, dict) else res
check("find over db.items still returns real rows",
      isinstance(_body, list) and len(_body) == 2, res)

res, _ = run_src(DB_SEED +
                  'retrieve one from db.items\n    match id to "2"\nretrieve: done\n'
                  'give back 200 one\n')
_body = res.get('body') if isinstance(res, dict) else res
check("retrieve over db.items still returns the matched row",
      isinstance(_body, dict) and _body.get('name') == 'Bo', res)

res, _ = run_src(DB_SEED +
                  'grab g from db.items\n    match id to "1"\ngrab: done\n'
                  'give back 200 g\n')
_body = res.get('body') if isinstance(res, dict) else res
check("grab over db.items still returns the matched row",
      isinstance(_body, dict) and _body.get('name') == 'Aria', res)


print("\n=== random from / repeat each over a held list: unchanged ===")

seen = set()
for _ in range(20):
    res, _ = run_src(HELD + 'pick random from colors\ngive back 200 pick\n')
    seen.add(res.get('body') if isinstance(res, dict) else res)
check("random from a held list still works and varies", seen <= {'red', 'blue', 'green'} and len(seen) > 1,
      str(seen))

res, it = run_src(HELD + 'repeat each c in colors\n    show c\nrepeat: done\n')
check("repeat each over a held list still works, in order",
      [str(x) for x in it.shown] == ['red', 'blue', 'green'], it.shown)


print("\n=== mio check does NOT catch this (runtime-only, by design -- see module docstring) ===")

from mohio_reachability import (scan_unreachable, scan_unwired, scan_orphan_it, scan_typos,
                                 scan_unknown_types, scan_undeclared_connectors,
                                 scan_not_built_services)

tree = P.parse(HELD + 'find matches in colors\nfind: done\nshow matches\n')
program = transform(tree, HELD)
all_errors = []
for scanner in (scan_unreachable, scan_unwired, scan_orphan_it, scan_typos, scan_unknown_types,
                scan_undeclared_connectors, scan_not_built_services):
    all_errors.extend(scanner(program) or [])
check("no static scanner flags find-over-held-list (confirms runtime-only, not an oversight)",
      len(all_errors) == 0, [str(e) for e in all_errors])


print("\n=== adversarial: the guard must not misfire on unrelated sources ===")

# A multi-part dotted source (`cache.settings`) is a DIFFERENT, intentionally-supported source
# shape (source_ref's own comment: "any dotted source", e.g. `get config from cache.settings").
# The guard only ever inspects single-part DottedName, so this must fall through unaffected --
# whatever it does (or does not do) is out of scope for this fix.
try:
    run_src('find x in cache.settings\nfind: done\nshow x\n')
    _multi_ok = True   # did not raise the held-source message; whatever else happened is fine
except MohioRuntimeError as e:
    _multi_ok = "held value" not in str(e)
except Exception:
    _multi_ok = True   # a parse/other error is not this guard misfiring
check("a multi-part dotted source (cache.settings) is not caught by the held-source guard",
      _multi_ok)

# A source name that is NOT a held variable at all (never assigned) must fall through to the
# EXISTING no_db_connection behavior unchanged -- ctx.exists() is False, so the new guard is a
# no-op and the old, correct-for-this-case message still fires. `no_db_connection` is raised via
# _Raise (not MohioRuntimeError), which it.run() catches internally and turns into a 500 response
# -- it does not propagate as a Python exception, unlike the new guard's refusal.
try:
    res, _ = run_src('find x in nonexistent_name\nfind: done\nshow x\n')
    _undefined_ok = (isinstance(res, dict) and res.get('status') == 500
                      and 'no_db_connection' in str(res.get('body', '')))
    _undefined_detail = res
except Exception as e:
    _undefined_ok = False
    _undefined_detail = f"{type(e).__name__}: {e}"
check("an undefined (never-held) source name still gets the ORIGINAL no_db_connection message",
      _undefined_ok, _undefined_detail)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
