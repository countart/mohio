# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""RUN 3 Part B1 (2026-08-19): connection-level on.failure/on.success, declared once on
`connect`, inherited by every operation on that connection unless it declares its own.

Grammar: `connect_decl` gained an additive `(result_handlers closer)?` group, mirroring
remove_all_block's own proven shape (mohio.lark:1708/1717) exactly -- the group is optional as
a WHOLE, so a bare `connect X as Y from Z` one-liner is completely untouched. Rides the shared
result_handlers mechanism every verb block uses (mohio_transformer_ast.py's result_handlers
transformer), not a parallel one.

Interpreter: a per-operation genuine error still fails loud by default (Part A/B's baseline,
unchanged); the connection-level on.failure is a DECLARE-ONCE OVERRIDE checked only after the
per-operation handler found nothing, so a per-use override always wins. connect only has a
STATE channel (on.failure/on.success -- did the connection succeed), never a CONDITION
(when/otherwise) -- there is no "found/empty" for a bare connection, so those fail loud at
check-time instead of silently never firing.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_run3_connection_handler.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform, MohioCompileError
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


def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    r = it.run(prog)
    return it, r


# ── B1: bare one-liner is completely unaffected (the additive proof) ───────────────────────
it, r = run_real('connect db as sqlite from env.DATABASE_URL\nshow "ok"\n')
check("bare bodyless connect one-liner still parses and runs unchanged",
      it.shown == ["ok"], it.shown)

# ── B1: block form, both inline-body and block-body handler shapes parse and run ───────────
it, r = run_real('connect db as sqlite from env.DATABASE_URL\n'
                  '    on.success show "connected"\nconnect: done\n')
check("inline on.success body works", it.shown == ["connected"], it.shown)

it, r = run_real('connect db as sqlite from env.DATABASE_URL\n'
                  '    on.success\n        show "connected"\nconnect: done\n')
check("block on.success body works", it.shown == ["connected"], it.shown)

# ── B1: connection-level on.failure is inherited by an operation with no handler of its own ─
it, r = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    '    on.failure\n        show "connection-level caught it"\nconnect: done\n'
    'retrieve item from db.ghost_table\n    match id to 1\nretrieve: done\n'
    'show "after"\n')
check("inherited connection-level on.failure catches a per-op error with no handler of its own",
      it.shown == ["connection-level caught it", "after"], it.shown)

# ── B1: per-operation on.failure overrides the connection-level one ────────────────────────
it, r = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    '    on.failure\n        show "connection-level (must NOT fire)"\nconnect: done\n'
    'retrieve item from db.ghost_table2\n    match id to 1\n'
    '    on.failure\n        show "per-op override fired"\n'
    'retrieve: done\nshow "after"\n')
check("a per-operation on.failure overrides the inherited connection-level one",
      it.shown == ["per-op override fired", "after"], it.shown)

# ── B1: with no connection-level handler at all, the Part A/B fail-loud baseline is untouched
it, r = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'retrieve item from db.ghost_table3\n    match id to 1\nretrieve: done\n'
    'show "unreachable"\n')
check("no connection handler -> still fails loud (Part A/B baseline unaffected)",
      r.get('status') == 500 and 'ghost_table3' in str(r.get('body', '')), r)

# ── B1: when/otherwise on connect (no CONDITION exists) fails loud at CHECK time ───────────
try:
    ast_transform(P.parse('connect db as sqlite from env.DATABASE_URL\n'
                          '    when true\n        show "x"\nconnect: done\n'),
                  'connect db as sqlite from env.DATABASE_URL\n')
    check("when on connect fails loud at check-time", False, "no error was raised")
except MohioCompileError as e:
    check("when on connect fails loud at check-time",
          'when/otherwise have no result to branch on' in str(e), str(e))

# ── B4: connection-level errors are audited via _audit_event ───────────────────────────────
it, r = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    '    on.failure\n        show "caught"\nconnect: done\n'
    'retrieve item from db.ghost_table4\n    match id to 1\nretrieve: done\n')
_log = it._audit_logs.get('security_audit_log', [])
check("a genuine operation error routed through the connection-level handler is audited",
      any(e.get('event') == 'operation_failed' for e in _log), _log)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
