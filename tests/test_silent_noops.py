# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Constructs that parse cleanly and then change nothing.

The failure class: a program compiles, the AST looks reasonable, and a value the developer wrote
was silently discarded. Parse-OK proves nothing here -- every construct below parsed before it was
fixed. The only test that finds this is compiling two programs that MUST behave differently and
asserting their ASTs differ.

Two root causes, both worth naming because the fixes differ:

  A. Alternatives built only from FILTERED terminals. Lark drops terminal names beginning with an
     underscore, so a rule whose alternatives contain nothing else collapses to the same empty
     subtree for every branch and the choice is unrecoverable. Fixed by aliasing each alternative
     so it gets its own handler.
     Affected: ai_create_type, time_bucket_unit, debug_mode.

  B. A rule subtree meeting a Token-only filter, or no handler at all. `trailing_qualifier` had a
     grammar rule and an AST slot and no transformer method, so the condition never moved between
     them and the field kept its default of None -- silently.

This is the same allowlist disease already in the catalog: a filter that does not name a thing
discards it. Every new construct with alternatives belongs in this test.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_g = '\n'.join(l for l in open(os.path.join(_ROOT, 'mohio.lark'), encoding='utf-8').read().splitlines()
               if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def ast(src):
    return repr(transform(P.parse(src), src).statements)


def run(src):
    it = MohioInterpreter()
    r = it.run(transform(P.parse(src), src))
    b = r.get('body') if isinstance(r, dict) else r
    return b.to_python() if hasattr(b, 'to_python') else b


C = 'connect db as sqlite from env.DATABASE_URL\n'

# ── trailing `if`: a guard that did not guard ─────────────────────────────────────────
# `give back "x" if <cond>` compiled byte-identically to the same statement with no `if` at all,
# so the response went out unconditionally. Anything gated this way -- a permission check, a
# partial response, a redaction -- was ungated while reading as handled.
check("trailing `if` reaches the AST on give back",
      'TrailingQualifier' in ast('give back 200 "x" if s is "a"\n'))
check("trailing `if` reaches the AST on halt",
      'TrailingQualifier' in ast('halt if s is "a"\n'))
check("trailing `if` reaches the AST on jump to",
      'TrailingQualifier' in ast('jump to /next if s is "a"\n'))
check("a guarded give back differs from an unguarded one",
      ast('give back 200 "x"\n') != ast('give back 200 "x" if s is "a"\n'))

# runtime: the half that actually changes behaviour
check("give back fires when the condition holds",
      run('s "yes"\ngive back 200 "fired" if s is "yes"\ngive back 200 "fell"\n') == 'fired')
check("give back does NOT fire when the condition fails",
      run('s "no"\ngive back 200 "fired" if s is "yes"\ngive back 200 "fell"\n') == 'fell')
check("halt halts only when the condition holds",
      run('s "yes"\nhalt if s is "yes"\ngive back 200 "reached"\n') == 'halted'
      and run('s "no"\nhalt if s is "yes"\ngive back 200 "reached"\n') == 'reached')
check("jump jumps only when the condition holds",
      run('s "no"\njump to /x if s is "yes"\ngive back 200 "reached"\n') == 'reached')

# ── ai.create type: the request itself was lost ───────────────────────────────────────
# `ai.create poster image` and `ai.create poster video` produced identical ASTs, and the field
# held the STRING FORM of an empty parse subtree -- a stringified Tree stored where downstream
# code reads data.
check("ai.create image and video are distinguishable",
      ast('ai.create p image\n') != ast('ai.create p video\n'))
check("ai.create carries its type value", "'image'" in ast('ai.create p image\n'))
check("no stringified parse tree is stored as data",
      'tree(' not in ast('ai.create p image\n').lower())

# ── time bucket: silently wrong numbers ───────────────────────────────────────────────
# Every bucket compiled the same, so a report grouped by year behaved exactly like one grouped
# by hour. Plausible-looking output is harder to notice than a crash.
_day = ast(C + 'find r in db.t\n    by day\nfind: done\n')
_year = ast(C + 'find r in db.t\n    by year\nfind: done\n')
check("`by day` and `by year` are distinguishable", _day != _year)
check("the bucket value is carried", "time_bucket='day'" in _day and "time_bucket='year'" in _year)

# ── debug mode ────────────────────────────────────────────────────────────────────────
check("debug modes are distinguishable", ast('debug on\n') != ast('debug verbose\n'))
check("debug carries its mode", "mode='verbose'" in ast('debug verbose\n'))

# ── a raw parse Tree in a finished AST always means a rule was never handled ──────────
# Worth keeping permanently and independently of any specific construct.
for _label, _src in (('debug', 'debug on\n'),
                     ('ai.create', 'ai.create p image\n'),
                     ('find by day', C + 'find r in db.t\n    by day\nfind: done\n'),
                     ('give back if', 'give back 200 "x" if s is "a"\n')):
    check(f"no raw parse Tree survives into the AST for {_label}",
          'Tree(' not in ast(_src), ast(_src)[:110])


# ── map modifiers: renamed AND actually carried ───────────────────────────────────────
# `map_modifier` is a RULE, so the child arriving at the parent was a subtree while the parent
# selected with `isinstance(c, Token) and c.type in (...)`. No subtree can satisfy that, so every
# modifier was silently discarded: two entries with opposite case rules compiled identically.
# Renamed at the same time so it lands once -- the new names say what they do, where `case.yes`
# and `case.no` needed the reader to already know what "yes" meant.
import re as _re


def _mods(src):
    _m = _re.search(r"modifiers=\[([^\]]*)\]", ast(src))
    return _m.group(1) if _m else '<no modifiers field>'


_M = 'map m\n    "a" -> "b" %s\nmap: done\n'
check("ignore.case is carried", _mods(_M % 'ignore.case') == "'ignore.case'",
      _mods(_M % 'ignore.case'))
check("match.case is carried", _mods(_M % 'match.case') == "'match.case'",
      _mods(_M % 'match.case'))
check("keep.whitespace is carried", _mods(_M % 'keep.whitespace') == "'keep.whitespace'",
      _mods(_M % 'keep.whitespace'))
check("modifiers combine", _mods(_M % 'match.case keep.whitespace')
      == "'match.case', 'keep.whitespace'", _mods(_M % 'match.case keep.whitespace'))
check("opposite case rules are distinguishable",
      ast(_M % 'ignore.case') != ast(_M % 'match.case'))
check("a modified entry differs from an unmodified one",
      ast(_M % 'ignore.case') != ast('map m\n    "a" -> "b"\nmap: done\n'))
check("the mapping's own values survive alongside the modifier",
      "value='a'" in ast(_M % 'ignore.case'))


def _retired(src):
    try:
        ast(src); return None
    except Exception as _e:
        return str(_e)


for _old, _new in (('case.no', 'ignore.case'), ('case.yes', 'match.case')):
    _err = _retired(_M % _old)
    check(f"`{_old}` is retired and fails loud", _err is not None)
    check(f"the `{_old}` message names `{_new}`", _err is not None and _new in _err,
          (_err or '')[:80])


# ── step 3: the trailing `if` proved END TO END through the real serving path ─────────
# Unit-level proof is not enough for this one. The failure mode was a permission gate that did
# not gate, so the proof has to be two requests differing only in the condition, answered by the
# real HTTP path, producing two different responses. Before the fix BOTH received the protected
# value.
from mohio_server import create_app, MohioServer
from starlette.testclient import TestClient

_GUARDED = ('shape Q\n    method GET\n    role as text\nshape: done\n'
            'listen for\n    request for sh.Q at /secret\n'
            '        give back 200 "CLASSIFIED" if role is "admin"\n'
            '        give back 200 "denied"\n'
            '    request: done\nlisten: done\n')
_prog = transform(P.parse(_GUARDED), _GUARDED)
_it = MohioInterpreter(); _it.run_declarations(_prog)
_client = TestClient(create_app(MohioServer(_prog, _it)))
_admin = _client.get('/secret?role=admin')
_guest = _client.get('/secret?role=guest')
check("e2e: the guarded response is returned when the condition holds",
      'CLASSIFIED' in _admin.text, _admin.text[:60])
check("e2e: the guarded response is NOT returned when the condition fails",
      'CLASSIFIED' not in _guest.text, _guest.text[:60])
check("e2e: two requests differing only in the condition get different responses",
      _admin.text != _guest.text)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
