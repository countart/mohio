#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Session-mode scope contract — regression guard.

Covers the bug where a `check`/`when`/`otherwise` branch that reassigns a request
field (e.g. `command`) in SESSION mode wrote the new value to the globally-shared
base context, where it was shadowed by the request field on the per-session
context — so later reads in the SAME request saw the OLD value. Zork symptom:
typing "n" should expand `command` to "go north" (verb "go"), but the trace read
verb "n" because the expansion never propagated.

These tests faithfully reproduce the scope chain that run_with_session builds:
    base  <-  session [_session_root=True]   (request fields set on `session`)
with _session_mode=True, then drive statements through the interpreter exactly as
the serving path does. They lock three guarantees:
  1. when/otherwise branch assignments propagate to later reads in the same request
  2. session state never leaks into the shared base context (cross-session safety)
  3. a value set in one request persists into the next request for the same session
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, MohioValue, _InMemorySessionStore

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def _stmts(prog):
    t = transform(P.parse(H + prog), H + prog)
    return [s for s in t.statements if type(s).__name__ != 'ConnectBlock']

def run_session(prog, seed, session=None, base=None):
    """Run statements in a faithful session scope. Returns (shown, base, session)."""
    it = MohioInterpreter(); it.shown = []; it._session_mode = True
    if base is None:
        base = Context()
    if session is None:
        session = base.child(); session._session_root = True
    for k, v in seed.items():
        session.set(k, MohioValue(v))
    for s in _stmts(prog):
        it._exec(s, session)
    return it.shown, base, session

# 1) when-branch reassigns request field; later read must see the new value
WHEN_PROG = ('check command\n'
             '    when "n"\n'
             '        command "go north"\n'
             '    check: done\n'
             'hold verb = command before " " default command\n'
             'show verb\n')
shown, base, _ = run_session(WHEN_PROG, {"command": "n"})
check("when-branch assignment propagates to later read", shown == ["go"])
check("no leak into shared base context", base.get("command").to_python() is None)

# 2) otherwise-branch behaves identically
OTHERWISE_PROG = ('check command\n'
                  '    when "x"\n'
                  '        command "nope"\n'
                  '    otherwise\n'
                  '        command "go south"\n'
                  '    check: done\n'
                  'hold verb = command before " " default command\n'
                  'show verb\n')
shown2, _, _ = run_session(OTHERWISE_PROG, {"command": "n"})
check("otherwise-branch assignment propagates", shown2 == ["go"])

# 3) plain (non-request-field) assignment in a branch also reads back
NEWVAR_PROG = ('check command\n'
               '    when "n"\n'
               '        destination "north_room"\n'
               '    check: done\n'
               'show destination\n')
shown3, _, _ = run_session(NEWVAR_PROG, {"command": "n"})
check("branch-set new var is readable later in request", shown3 == ["north_room"])

# 4) cross-request persistence: a value set in request 1 survives into request 2
base = Context(); session = base.child(); session._session_root = True
REQ1 = ('check flag\n'
        '    when "set"\n'
        '        remembered "yes"\n'
        '    check: done\n')
run_session(REQ1, {"flag": "set"}, session=session, base=base)
REQ2 = ('hold out = remembered default "forgotten"\n'
        'show out\n')
shown4, _, _ = run_session(REQ2, {"flag": "other"}, session=session, base=base)
check("value persists across requests in same session", shown4 == ["yes"])

# 5) END-TO-END through the real run_with_session serving path (not just _exec).
#    Listener model is shape-based: `new sh.X` matches POST-like requests.
def _e2e(prog_body, requests):
    prog = H + prog_body
    t = transform(P.parse(prog), prog)
    sessions = _InMemorySessionStore()
    bodies = []
    for req in requests:
        r = MohioInterpreter().run_with_session(t, req, "e2e", sessions)
        bodies.append(r.get('body') if isinstance(r, dict) else r)
    return bodies

MOVE = ('listen for\n'
        '    new sh.Move\n'
        '        check command\n'
        '            when "n"\n'
        '                command "go north"\n'
        '            check: done\n'
        '        hold verb = command before " " default command\n'
        '        give back verb\n'
        '    new: done\n'
        'listen: done\n')
check("e2e: command expansion via run_with_session",
      _e2e(MOVE, [{"_method": "POST", "command": "n"}]) == ["go"])
check("e2e: non-matching command passes through",
      _e2e(MOVE, [{"_method": "POST", "command": "s"}]) == ["s"])

# A per-session value set in request 1 persists into request 2. Note: the handler does NOT
# re-`hold` the value each request -- `hold` is per-session (OQ-011), so re-holding a value
# already held in an earlier request of the same session fails loud ("already held"). The
# persisted value is simply read back with a `default` fallback for the first request.
VISIT = ('listen for\n'
         '    new sh.Visit\n'
         '        check flag\n'
         '            when "set"\n'
         '                remembered "yes"\n'
         '            check: done\n'
         '        give back remembered default "first"\n'
         '    new: done\n'
         'listen: done\n')
check("e2e: state persists across requests via run_with_session",
      _e2e(VISIT, [{"_method": "POST", "flag": "set"},
                   {"_method": "POST", "flag": "other"}]) == ["yes", "yes"])

# OQ-011 guard: `hold` is per-session. Re-holding a value already held earlier in the SAME session
# fails loud rather than silently re-binding -- that is the correct behavior (a held value is
# released with `release`, not silently overwritten by a second `hold`).
REHOLD = ('listen for\n'
          '    new sh.Visit\n'
          '        hold once = "a"\n'
          '        give back once\n'
          '    new: done\n'
          'listen: done\n')
try:
    _rehold = _e2e(REHOLD, [{"_method": "POST"}, {"_method": "POST"}])
    # if it returned, request 2 must NOT have silently re-bound to "a"
    _reheld_ok = (_rehold[0] == "a" and _rehold[1] != "a")
except Exception as _e:
    # a raised "already held" on the second request is also correct fail-loud behavior
    _reheld_ok = "already held" in str(_e)
check("OQ-011: re-hold across requests in one session fails loud", _reheld_ok)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
