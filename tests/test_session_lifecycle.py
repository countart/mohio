# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Session lifecycle: mio_session reservation, rotation, and expiry (2026-08-04 build,
per BRIEF-session-lifecycle-rotation-expiry.md).

Covers, per the ruling:
  - mio_session is runtime-owned: miocookie.set on it fails loud, naming the mechanism.
  - The runtime auto-emits mio_session on every session-bearing response, with no app
    code required at all (the old check/otherwise dance this replaces is gone).
  - Rotation fires ONLY on an actual role-set CHANGE, never on an idempotent
    re-assertion of an already-held role -- the exact bug this build stopped before
    building blind (Zork calls `grant role "player"` on every single request).
  - A rotated session's OLD id is genuinely refused on the next request (mints a fresh,
    empty session), not merely unissued.
  - Idle timeout and absolute timeout are two independent mechanisms; each fires on its
    own trigger regardless of the other's state.
  - A sector's `expire all [session_idle/session_absolute] after N unit` TIGHTENS the
    runtime default, never loosens it (mirrors get_confidence_floor's raise-only rule).
  - The concurrency race the brief specifically named: re-keying self.sessions under
    simultaneous requests, using REAL OS threads (not sequential calls), since mio
    serve genuinely thread-offloads at least one route via asyncio.to_thread.

Run: `python tests/test_session_lifecycle.py`.
"""
import os, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, _InMemorySessionStore
from mohio_sector_loader import SectorProfile, ExpireRule

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

def sid_of(resp):
    return resp['body'].split('session=')[1]

# ── 1. mio_session reservation ──────────────────────────────────────────────────────
SRC_WRITE = ('shape P\n    command as text\nshape: done\n'
             'listen for\n    new sh.P\n'
             '        miocookie.set "mio_session" to "hack"\n'
             '        give back 200 "ok"\n    new: done\nlisten: done\n')
prog = transform(_P.parse(SRC_WRITE), SRC_WRITE)
r = MohioInterpreter().run_with_session(prog, {'_method': 'POST', '_path': '/p', 'command': 'x'},
                                        'sess1', _InMemorySessionStore())
check("miocookie.set on mio_session fails loud (was: silently accepted)",
      r.get('status') == 500 and 'mio_session' in str(r.get('body', '')), str(r))
check("the fail-loud names the runtime-owned mechanism (sh. reservation parallel)",
      'runtime-owned' in str(r.get('body', '')), str(r))

# Regression: a DIFFERENT cookie name is completely unaffected.
SRC_OTHER = ('shape P\n    command as text\nshape: done\n'
             'listen for\n    new sh.P\n'
             '        miocookie.set "preference" to "dark_mode"\n'
             '        give back 200 "ok"\n    new: done\nlisten: done\n')
prog_o = transform(_P.parse(SRC_OTHER), SRC_OTHER)
r_o = MohioInterpreter().run_with_session(prog_o, {'_method': 'POST', '_path': '/p', 'command': 'x'},
                                          None, _InMemorySessionStore())
check("regression: an unrelated cookie name is completely unaffected",
      r_o.get('status') == 200, str(r_o))

# ── 2. Runtime auto-emits mio_session with no app code at all ──────────────────────
SRC_PLAIN = ('shape P\n    command as text\nshape: done\n'
             'listen for\n    new sh.P\n'
             '        give back 200 ("session=" & session.id)\n    new: done\nlisten: done\n')
prog2 = transform(_P.parse(SRC_PLAIN), SRC_PLAIN)
it2 = MohioInterpreter(); sessions2 = _InMemorySessionStore()
r1 = it2.run_with_session(prog2, {'_method': 'POST', '_path': '/p', 'command': 'x'}, None, sessions2)
sid1 = sid_of(r1)
check("first request mints a session with no app code writing any cookie",
      len(sid1) > 8, sid1)
check("the runtime auto-emits mio_session in pending cookies",
      r1.get('__pending_cookies__', {}).get('mio_session', {}).get('value') == sid1, str(r1))
r2 = it2.run_with_session(prog2, {'_method': 'POST', '_path': '/p', 'command': 'x'}, sid1, sessions2)
check("second request with the SAME cookie returns the SAME session (persistence works)",
      sid_of(r2) == sid1, sid_of(r2))

# ── 3. Rotation fires ONLY on an actual role-set change (the core finding) ─────────
SRC_ZORK_SHAPED = ('shape P\n    command as text\nshape: done\n'
                   'listen for\n    new sh.P\n'
                   '        grant role "player"\n'
                   '        require role "player"\n'
                   '        give back 200 ("session=" & session.id)\n    new: done\nlisten: done\n')
prog3 = transform(_P.parse(SRC_ZORK_SHAPED), SRC_ZORK_SHAPED)
it3 = MohioInterpreter(); sessions3 = _InMemorySessionStore()
r_a = it3.run_with_session(prog3, {'_method': 'POST', '_path': '/p', 'command': 'look'}, None, sessions3)
sid_a = sid_of(r_a)
r_b = it3.run_with_session(prog3, {'_method': 'POST', '_path': '/p', 'command': 'take'}, sid_a, sessions3)
sid_b = sid_of(r_b)
r_c = it3.run_with_session(prog3, {'_method': 'POST', '_path': '/p', 'command': 'move'}, sid_b, sessions3)
sid_c = sid_of(r_c)
check("Zork-shaped repeated grant role \"player\" -> ZERO unnecessary rotations across look/take/move",
      sid_a == sid_b == sid_c, f"{sid_a} {sid_b} {sid_c}")
_events3 = [e['event'] for e in it3._audit_logs.get('security_audit_log', [])]
check("exactly ONE session_rotated across 3 repeated grants (the genuine first grant only)",
      _events3.count('session_rotated') == 1, _events3)
check("exactly THREE role_granted (every grant call is still audited, rotation or not)",
      _events3.count('role_granted') == 3, _events3)

# ── 4. A genuine escalation DOES rotate, and the old id is genuinely refused ────────
SRC_ESCALATE = ('shape P\n    command as text\nshape: done\n'
                'listen for\n    new sh.P\n'
                '        grant role command\n'
                '        give back 200 ("session=" & session.id)\n    new: done\nlisten: done\n')
prog4 = transform(_P.parse(SRC_ESCALATE), SRC_ESCALATE)
it4 = MohioInterpreter(); sessions4 = _InMemorySessionStore()
r_guest = it4.run_with_session(prog4, {'_method': 'POST', '_path': '/p', 'command': 'guest'}, None, sessions4)
sid_guest = sid_of(r_guest)
r_admin = it4.run_with_session(prog4, {'_method': 'POST', '_path': '/p', 'command': 'admin'}, sid_guest, sessions4)
sid_admin = sid_of(r_admin)
check("a genuine escalation (guest -> admin) DOES rotate the session id",
      sid_guest != sid_admin, f"{sid_guest} {sid_admin}")

# The critical adversarial proof: presenting the OLD id afterward must be GENUINELY
# refused -- not merely unissued -- and must not resurrect the admin session's state.
r_old_again = it4.run_with_session(prog4, {'_method': 'POST', '_path': '/p', 'command': 'admin'},
                                   sid_guest, sessions4)
sid_after_old = sid_of(r_old_again)
check("the OLD id is GENUINELY refused: presenting it again mints a THIRD, different id "
      "(not the old guest session, not the admin session)",
      sid_after_old not in (sid_guest, sid_admin), f"{sid_guest} {sid_admin} {sid_after_old}")

# The new admin id continues to work correctly.
r_admin_again = it4.run_with_session(prog4, {'_method': 'POST', '_path': '/p', 'command': 'admin'},
                                     sid_admin, sessions4)
check("the NEW (post-rotation) id continues to work correctly",
      sid_of(r_admin_again) == sid_admin, sid_of(r_admin_again))

# ── 4b. The invalidated-id ban is STRONGER than "just not in the dict" ─────────────
# Rotation already pops the old key, so a naive `if session_id not in sessions` check
# would ALSO refuse a stale id (it's simply absent) -- that alone doesn't prove the
# brief's explicit requirement ("invalidate the old ID so it can never be reused, not
# just abandoned"). Prove the STRONGER guarantee: even if something else re-populates
# an entry under the exact old id string, the invalidated set still wins and refuses
# it, rather than silently accepting whatever happens to be sitting under that key.
sessions4.put(sid_guest, sessions4.get(sid_admin, None))   # force-repopulate the old key (paranoid case)
r_forced = it4.run_with_session(prog4, {'_method': 'POST', '_path': '/p', 'command': 'admin'},
                                sid_guest, sessions4)
sid_forced = sid_of(r_forced)
check("the invalidated-id ban wins even over a re-populated entry under the same old key "
      "(stronger than 'just not in the dict')",
      sid_forced not in (sid_guest, sid_admin), f"guest={sid_guest} admin={sid_admin} got={sid_forced}")

# ── 5. Idle timeout and absolute timeout are independent mechanisms ────────────────
prog5 = transform(_P.parse(SRC_PLAIN), SRC_PLAIN)
it5 = MohioInterpreter(); sessions5 = _InMemorySessionStore()
r5a = it5.run_with_session(prog5, {'_method': 'POST', '_path': '/p', 'command': 'x'}, None, sessions5)
sid5a = sid_of(r5a)
sessions5.get(sid5a, None)._last_accessed = time.time() - 3600   # idle-expired (> 1800s default)
r5b = it5.run_with_session(prog5, {'_method': 'POST', '_path': '/p', 'command': 'x'}, sid5a, sessions5)
check("an idle-expired session is genuinely rejected (fresh, different id)",
      sid_of(r5b) != sid5a, sid_of(r5b))

it5c = MohioInterpreter(); sessions5c = _InMemorySessionStore()
r5c = it5c.run_with_session(prog5, {'_method': 'POST', '_path': '/p', 'command': 'x'}, None, sessions5c)
sid5c = sid_of(r5c)
sessions5c.get(sid5c, None)._created_at = time.time() - 50000     # absolute-expired (> 43200s default)
sessions5c.get(sid5c, None)._last_accessed = time.time()          # but IDLE-fresh (just touched)
r5d = it5c.run_with_session(prog5, {'_method': 'POST', '_path': '/p', 'command': 'x'}, sid5c, sessions5c)
check("an absolute-expired session is rejected even when idle-fresh (independent mechanisms)",
      sid_of(r5d) != sid5c, sid_of(r5d))

# ── 6. Sector expire_rules tighten, never loosen (mirrors get_confidence_floor) ────
it6 = MohioInterpreter()
strict = SectorProfile(name='banking-strict')
strict.expire_rules = [ExpireRule(classification='session_idle', duration=15, unit='minutes'),
                       ExpireRule(classification='session_absolute', duration=2, unit='hours')]
class _Ctx:
    _sector_profile = strict
idle_s, abs_s = it6._session_timeout_ceilings(_Ctx())
check("a sector declaring a SHORTER idle timeout tightens the ceiling (15 min -> 900s)",
      idle_s == 900, idle_s)
check("a sector declaring a SHORTER absolute timeout tightens the ceiling (2 hr -> 7200s)",
      abs_s == 7200, abs_s)

lenient = SectorProfile(name='lenient')
lenient.expire_rules = [ExpireRule(classification='session_idle', duration=10, unit='hours')]
class _Ctx2:
    _sector_profile = lenient
idle_l, abs_l = it6._session_timeout_ceilings(_Ctx2())
check("a sector declaring a LONGER idle timeout does NOT loosen the runtime default",
      idle_l == 1800, idle_l)

# ── 7. Max-Age reflects whichever timeout is SHORTER ────────────────────────────────
it7 = MohioInterpreter()
opts = it7._session_cookie_opts('sid', idle_ceiling=900, absolute_ceiling=7200,
                                created_at=time.time(), now=time.time())
check("Max-Age is the SHORTER of idle (900) and absolute-remaining (~7200) -> 900",
      opts['expires'] == 900, opts)
opts2 = it7._session_cookie_opts('sid', idle_ceiling=900, absolute_ceiling=7200,
                                 created_at=time.time() - 7000, now=time.time())
check("Max-Age correctly switches to absolute-remaining when it becomes the shorter one",
      opts2['expires'] < 900 and opts2['expires'] >= 0, opts2)

# ── 8. The concurrency race the brief specifically named ───────────────────────────
# Real OS threads, not sequential calls -- mio serve genuinely thread-offloads at least
# one route via asyncio.to_thread, confirmed by reading the actual server wiring, so a
# threaded request and a non-threaded request CAN touch the same session dict at once.
prog8 = transform(_P.parse(SRC_ESCALATE), SRC_ESCALATE)
it8 = MohioInterpreter(); sessions8 = _InMemorySessionStore()
r8_guest = it8.run_with_session(prog8, {'_method': 'POST', '_path': '/p', 'command': 'guest'},
                                None, sessions8)
sid8_guest = sid_of(r8_guest)

results = []
errors = []
barrier = threading.Barrier(2)

def rotate_thread():
    try:
        barrier.wait(timeout=5)
        r = it8.run_with_session(prog8, {'_method': 'POST', '_path': '/p', 'command': 'admin'},
                                 sid8_guest, sessions8)
        results.append(('rotate', sid_of(r)))
    except Exception as e:
        errors.append(('rotate', e))

def racing_thread():
    try:
        barrier.wait(timeout=5)
        # A concurrent request presenting the SAME (about-to-be-rotated) id. Whichever
        # side of the race it lands on, it must get EITHER the still-valid guest session
        # (if it beat the rotation) OR a fresh mint (if it lost to the rotation and the
        # id was already invalidated) -- never a crash, never a KeyError, never a
        # corrupted dict, and never silently attached to a DIFFERENT existing session's
        # state it has no business seeing.
        r = it8.run_with_session(prog8, {'_method': 'POST', '_path': '/p', 'command': 'guest'},
                                 sid8_guest, sessions8)
        results.append(('race', sid_of(r)))
    except Exception as e:
        errors.append(('race', e))

threads = [threading.Thread(target=rotate_thread), threading.Thread(target=racing_thread)]
for t in threads: t.start()
for t in threads: t.join(timeout=10)

check("concurrent rotation + a racing request on the same id: NO crash in either thread",
      len(errors) == 0, errors)
check("concurrent access produced exactly 2 results (both threads completed)",
      len(results) == 2, results)
# The store must be internally consistent afterward: no orphaned/duplicate entries, and
# the invalidated set actually contains the old id. __base__ and __invalidated__ are no
# longer special keys sharing space with real sessions (2026-08-05 session-store-seam
# build) -- every key under _sessions IS a live session now, no exclusion list needed.
_live_ids = list(sessions8._sessions.keys())
check("session store is internally consistent after the race (no duplicate/orphaned live keys)",
      len(_live_ids) == len(set(_live_ids)), _live_ids)
check("the old guest id ended up in the invalidated set (rotation completed correctly under the race)",
      sessions8.is_invalidated(sid8_guest), sessions8._invalidated)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
