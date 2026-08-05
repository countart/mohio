# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""A variable whose name STARTS with a keyword must not be split.

Keyword terminals were bare strings with no word boundary, so Earley's dynamic lexer split
identifiers on them:

    set_skip "ok"   ->  SET + NAME(_skip)      i.e.  set _skip "ok"
    holder 5        ->  HOLD + NAME(older)     i.e.  hold older 5     <-- silent-wrong
    lockbox 5       ->  LOCK + NAME(box)       i.e.  lock box 5       <-- silent-wrong

`holder 5` quietly declared a HELD variable named `older`. This shipped in the language and
was only caught because the Zork demo has a variable called `set_skip`. The declaration
keywords are now anchored (`/hold(?![A-Za-z0-9_])/`).
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark, Token

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def toks(src):
    return [c.type for c in _P.parse(src).scan_values(lambda v: isinstance(v, Token))]

# identifiers that begin with a keyword stay ONE name
check("`set_skip` is one NAME (Zork uses it)", toks('set_skip "ok"\n')[0] == 'NAME')
check("`holder` is one NAME, not hold+older",  toks('holder 5\n')[0] == 'NAME')
check("`lockbox` is one NAME, not lock+box",   toks('lockbox 5\n')[0] == 'NAME')
# the real keywords still lex as keywords
check("`hold x 5` still lexes HOLD",           toks('hold x 5\n')[0] == 'HOLD')
check("`lock k 3` still lexes LOCK",           toks('lock k 3\n')[0] == 'LOCK')
check("`set x 5` still lexes SET (then rejected)", toks('set x 5\n')[0] == 'SET')

# ── The 2026-07-12 sweep: 146 named keyword terminals given a word boundary. ──────────────
# A reserved word is reserved -- it may not be the front half of somebody's identifier.
# These are the block openers, the ones that actually caused silent mis-parses.
for _n in ('checklist', 'checkout', 'findings', 'finder', 'taskmaster', 'retrieval',
           'eachother', 'makeshift', 'showcase', 'sender', 'tryout', 'matcher',
           'iterate', 'starter', 'modifier', 'release_note', 'asset', 'forth'):
    check(f"`{_n}` is one NAME, not a keyword + tail", toks(f'{_n} 5\n')[0] == 'NAME')

# ...and the keywords themselves still lex as keywords.
check("`check` still lexes CHECK",
      toks('x 5\ncheck x\n    when x is more than 3\n        show "big"\ncheck: done\n')[2] == 'CHECK')
check("`find` still lexes FIND",
      toks('find u in db.users\nfind: done\n')[0] == 'FIND')
check("`each` still lexes EACH",
      toks('each item in items\n    show item\neach: done\n')[0] == 'EACH')

# Dotted keywords must NOT be split by the boundary -- this is what broke the naive sweep:
# a plain (?![A-Za-z0-9_]) still permits a following dot, so `starts.with` became STARTS + .with.
check("`starts.with` stays ONE token",     'STARTS_WITH' in toks('x "a"\nshow "y" if x starts.with "a"\n'))
check("`ends.with` stays ONE token",       'ENDS_WITH'   in toks('x "a"\nshow "y" if x ends.with "a"\n'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
