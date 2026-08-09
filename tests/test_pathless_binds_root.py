# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T0-3: a pathless listener (`new sh.X` / `request for sh.X` with no `at`) binds to `/`
EXACTLY -- it is the designed root handler, not a wildcard that answers every path.

THE BUG (`_exec_ListenBlock`'s single-endpoint fallback, mohio_interpreter.py, formerly ~line
4479, now ~4499):

    if len(candidates) == 1 and (req_path is None or candidates[0].path is None):
        return _dispatch(candidates[0])

`candidates[0].path is None` fired for ANY req_path -- garbage included -- as long as the
listener was the only candidate for that method. `GET /totally-made-up-path` dispatched a
pathless listener and, for a write body, wrote a row.

THE FIX: the pathless branch now additionally requires the request path to normalize to `/`.
`req_path is None` (the caller pinned no path at all -- e.g. `mio run --request-file`) is
untouched, a genuinely different case. A path-pinned listener whose path does not match
`req_path` already fell through to the 404 below before this fix and still does -- unaffected.

Uses `it.run(program, request=...)` directly (not a subprocess/real HTTP server) for speed; the
real-HTTP round trip (curl against `mio serve`, before-and-after the fix, POST / dispatching and
writing, POST to a garbage path 404ing and NOT writing) was run and pasted at the review
checkpoint for this unit -- this file is the regression lock, not the first proof.

SIBLING, SAME UNIT, FOUND WHILE BUILDING THIS FILE, NOW ALSO FIXED: a client sending an explicit
`_shape` field in its JSON body (`{"_shape": "Ping", ...}`) reaches the SEPARATE shape-dispatch
branch (step 2, above the single-endpoint fallback) whenever no candidate for the method declares
any path -- that branch used to dispatch on shape alone, ignoring req_path entirely. Confirmed
live over real HTTP: `POST /this/is/garbage` with `{"_shape":"Ping"}` returned 200 and wrote a
row, even with the fallback fix already applied. Fixed the same way: the shape-selected candidate
now has to pass the identical path check as any other dispatch -- pathless binds to `/` only, a
path-pinned candidate must match req_path exactly. `_shape` still SELECTS which candidate; it can
no longer overrule the path check. Real clients (mohio_server.py) only ever set `_shape` when the
request body itself carries that field, so an ordinary POST with no `_shape` is unaffected either
way -- covered by the checks above, which never set it.

Run: `python tests/test_pathless_binds_root.py`.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

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


PATHLESS_SRC = '''connect db as sqlite from env.DATABASE_URL

shape Ping
    note as text
shape: done

listen for
    new sh.Ping
        save to db.hits
            note ping.note
        save: done
        give back 200 "root ok"
    new: done
listen: done
'''

PINNED_SRC = '''connect db as sqlite from env.DATABASE_URL

shape Hit
    note as text
shape: done

listen for
    new sh.Hit at /hit
        save to db.hits
            note hit.note
        save: done
        give back 200 "hit ok"
    new: done
listen: done
'''


def fresh(src):
    prog = transform(P.parse(src), src)
    it = MohioInterpreter(db_path=':memory:')
    it.run_declarations(prog)
    return prog, it


def rows(it):
    return [dict(r) for r in it._db.conn.execute("select note from hits").fetchall()]


print("=== pathless listener: binds to / exactly ===")

prog, it = fresh(PATHLESS_SRC)
r = it.run(prog, request={"_method": "POST", "_path": "/", "note": "root-note"})
check("POST / dispatches the pathless listener", r == {'status': 200, 'body': 'root ok'}, r)
check("the write body actually ran (row landed)", rows(it) == [{'note': 'root-note'}], rows(it))

r2 = it.run(prog, request={"_method": "POST", "_path": "/totally-made-up-path",
                            "note": "should-not-land"})
check("POST /garbage-path is refused (404), not dispatched",
      isinstance(r2, dict) and r2.get('status') == 404, r2)
check("the refused request's write body did NOT run (row count unchanged)",
      rows(it) == [{'note': 'root-note'}], rows(it))

# A different garbage path, same story -- not a one-off.
r3 = it.run(prog, request={"_method": "POST", "_path": "/../../etc/passwd",
                            "note": "should-also-not-land"})
check("a second, differently-shaped garbage path is also refused",
      isinstance(r3, dict) and r3.get('status') == 404, r3)
check("still no extra row after the second refused request",
      rows(it) == [{'note': 'root-note'}], rows(it))

# req_path is None (no _path key at all -- e.g. mio run --request-file): untouched by this fix,
# a genuinely different case, must keep dispatching the lone candidate.
prog4, it4 = fresh(PATHLESS_SRC)
r4 = it4.run(prog4, request={"_method": "POST", "note": "no-path-key"})
check("no _path key at all still dispatches the lone candidate (unaffected case)",
      r4 == {'status': 200, 'body': 'root ok'}, r4)


print("\n=== path-pinned listener (`at /hit`): unchanged ===")

prog5, it5 = fresh(PINNED_SRC)
r5 = it5.run(prog5, request={"_method": "POST", "_path": "/hit", "note": "pinned-note"})
check("POST /hit dispatches the pinned listener", r5 == {'status': 200, 'body': 'hit ok'}, r5)
check("the write body ran", rows(it5) == [{'note': 'pinned-note'}], rows(it5))

r6 = it5.run(prog5, request={"_method": "POST", "_path": "/garbage", "note": "x"})
check("a pinned listener still 404s on a garbage path",
      isinstance(r6, dict) and r6.get('status') == 404, r6)
check("no write from the garbage request against a pinned listener",
      rows(it5) == [{'note': 'pinned-note'}], rows(it5))

# Adversarial: a path-pinned listener must NOT fall back to answering / either -- pathless
# binding to / is specific to a listener that declares no path at all.
r7 = it5.run(prog5, request={"_method": "POST", "_path": "/", "note": "y"})
check("a path-pinned listener does NOT also answer / (pathless-binds-root is not a general "
      "single-candidate fallback)",
      isinstance(r7, dict) and r7.get('status') == 404, r7)
check("no write from the / request against a pinned listener",
      rows(it5) == [{'note': 'pinned-note'}], rows(it5))


print("\n=== the _shape bypass (step 2, shape-dispatch branch): fixed alongside the fallback ===")

# Legitimate case: an explicit _shape to a pathless listener's home path (/) still works --
# _shape still SELECTS the candidate, only the path check is new.
prog8, it8 = fresh(PATHLESS_SRC)
r8 = it8.run(prog8, request={"_method": "POST", "_path": "/", "_shape": "Ping",
                              "note": "shape-legit-root"})
check("explicit _shape to a pathless listener's / still dispatches",
      r8 == {'status': 200, 'body': 'root ok'}, r8)
check("the write body ran", rows(it8) == [{'note': 'shape-legit-root'}], rows(it8))

# The bypass itself: explicit _shape + a garbage path must now be refused, not dispatched.
r9 = it8.run(prog8, request={"_method": "POST", "_path": "/this/is/garbage", "_shape": "Ping",
                              "note": "shape-bypass-should-not-land"})
check("explicit _shape + a garbage path is now refused (404), not dispatched",
      isinstance(r9, dict) and r9.get('status') == 404, r9)
check("the bypass attempt's write did NOT run (row count unchanged)",
      rows(it8) == [{'note': 'shape-legit-root'}], rows(it8))

# Path-pinned listener + explicit _shape: correct path still works, garbage path still refused --
# _shape does not grant a path-pinned listener a bypass either.
prog10, it10 = fresh(PINNED_SRC)
r10 = it10.run(prog10, request={"_method": "POST", "_path": "/hit", "_shape": "Hit",
                                 "note": "pinned-shape-correct"})
check("explicit _shape to a path-pinned listener's correct path still dispatches",
      r10 == {'status': 200, 'body': 'hit ok'}, r10)
check("the write body ran", rows(it10) == [{'note': 'pinned-shape-correct'}], rows(it10))

r11 = it10.run(prog10, request={"_method": "POST", "_path": "/garbage", "_shape": "Hit",
                                 "note": "pinned-shape-bypass"})
check("explicit _shape + a garbage path against a path-pinned listener is still refused",
      isinstance(r11, dict) and r11.get('status') == 404, r11)
check("no write from the pinned+_shape bypass attempt",
      rows(it10) == [{'note': 'pinned-shape-correct'}], rows(it10))


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
