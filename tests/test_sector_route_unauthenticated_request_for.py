# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""scan_sector_route_unauthenticated (mohio_reachability.py) never covered `request for sh.X`
routes -- the same disease the T1-SILENT-SWEEP-BATCH11 dead-code pass was named for. The
scanner's `walk()` checked `type(node).__name__ in ('PageDecl', 'ListenerDecl', 'RequestBlock',
'NewBlock')`. `RequestBlock` is a bare Python-level alias for `RequestInboundBlock`
(`mohio_ast.py`, `RequestBlock = RequestInboundBlock`) -- a class's `__name__` is fixed at
definition and can never equal an alias assigned to it elsewhere, so this string never matched
anything. `ListenerDecl` does not exist as a class anywhere in this codebase at all. Both dead
strings were removed in BATCH11 (a pure deletion, no coverage change); this fix ADDS the real
name, `RequestInboundBlock`, restoring the coverage the dead string always failed to provide.

Reachability rule (verified against `_exec_ListenBlock`/`_method_ok`, mohio_interpreter.py): a
`request for sh.X [at /path]` listener reaches its body through the identical listener-dispatch
mechanism `new sh.X` uses for POST -- the same `listen for` container, the same candidate-filter
-> path/shape/fallback routing -- just gated to GET/REQUEST instead of POST/NEW/PUT. The scanner
does no deeper reachability analysis for its existing PageDecl/NewBlock coverage either (a pure
type-match anywhere in the tree, no check that a listener sits inside a dispatchable `listen
for`), so RequestInboundBlock is covered the same way, not a bespoke stricter rule.

Corpus sweep (2026-08-15, all of examples/, cookbook/, start-here/, tests/, drafts/, tests/zork/):
zero new findings. Only 4 files use `request for sh.` at all; 2 declare no sector (scanner exits
before reaching route-type checks, unaffected either way); the other 2
(`tests/fraud_demo_full.mho`, `drafts/school-checkin-starter.mho`) both already declare
`require role` inside their `request for` block -- confirmed by direct source read (both hit an
unrelated, pre-existing parse error blocking the automated scan itself, one from `build
fraud_cache` syntax and one from a retired `: done as NAME` closer form -- neither related to
this fix; manual verification closes the gap the parse errors leave in the automated sweep).

Run: `python tests/test_sector_route_unauthenticated_request_for.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_reachability import scan_sector_route_unauthenticated

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

def scan(src):
    prog = transform(P.parse(src), src)
    return scan_sector_route_unauthenticated(prog)


UNPROTECTED = """\
sector: financial
connect db as sqlite from env.DATABASE_URL

shape Acct
    method GET
shape: done

listen for
    request for sh.Acct at /balance
        retrieve acct from db.accounts
            match id to 1
        retrieve: done
        give back 200 acct
    request: done
listen: done
"""

PROTECTED = """\
sector: financial
connect db as sqlite from env.DATABASE_URL

shape Acct
    method GET
shape: done

listen for
    request for sh.Acct at /balance
        require role "member"
        retrieve acct from db.accounts
            match id to 1
        retrieve: done
        give back 200 acct
    request: done
listen: done
"""

warnings_unprotected = scan(UNPROTECTED)
check("a dispatchable `request for` route reading sector data with NO require role IS flagged "
      "(the gap this fix closes -- was silently never covered before)",
      any('/balance' in w.message and 'financial' in w.message for w in warnings_unprotected),
      [w.message for w in warnings_unprotected])

warnings_protected = scan(PROTECTED)
check("the identical `request for` route WITH require role is NOT flagged "
      "(a real, correctly-guarded route must not false-positive)",
      not warnings_protected, [w.message for w in warnings_protected])

# Regression: the existing PageDecl/NewBlock coverage this scanner already had is unaffected.
PAGE_UNPROTECTED = """\
sector: financial
connect db as sqlite from env.DATABASE_URL

page Balance at /balance
    retrieve acct from db.accounts
        match id to 1
    retrieve: done
    render
        <p>{{ acct }}</p>
    render: done
page: done
"""
warnings_page = scan(PAGE_UNPROTECTED)
check("regression: the existing `page` route coverage still fires unaffected",
      any('/balance' in w.message for w in warnings_page), [w.message for w in warnings_page])

NEW_UNPROTECTED = """\
sector: financial
connect db as sqlite from env.DATABASE_URL

shape Deposit
    amount as decimal required
shape: done

listen for
    new sh.Deposit at /deposit
        save to db.accounts
            amount request.amount
        save: done
        give back 201 "ok"
    new: done
listen: done
"""
warnings_new = scan(NEW_UNPROTECTED)
check("regression: the existing `new sh.X` route coverage still fires unaffected",
      any('/deposit' in w.message for w in warnings_new), [w.message for w in warnings_new])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
