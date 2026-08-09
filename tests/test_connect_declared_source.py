# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`connect db as sqlite from env.X` now actually consults that env var (2026-08-05).

Two compounding bugs, fixed together:

1. `mohio_transformer_ast.py`'s `connect_decl` grammar embeds ENV_REF/SECRET_REF as raw
   terminals directly in the rule (not via the separate env_ref/secret_ref sub-rules,
   which DO build a real EnvRef/SecretRef) -- so `ConnectDecl.source` was `None` for
   EVERY connect declaration ever parsed, regardless of driver. Verified live: even the
   canonical `connect db as sqlite from env.DATABASE_URL` produced `node.source is None`.
2. `mohio_interpreter.py`'s `_exec_ConnectDecl` passed `self.db_path` (the interpreter
   constructor's own default) to `_make_db_runtime` for the sqlite driver
   unconditionally -- it never read `node.source` at all, so even with bug 1 fixed
   alone, the declared env var still would not have been consulted.

Invisible through `mio run`/`mio serve` (the CLI resolves and passes `db_path=` to the
constructor before running), but a real gap for any direct-Python caller -- found while
building a query-scaling measurement harness that relied on the declared source.

postgres/mysql/mongo are unaffected: they already read `os.environ` directly inside
`_make_db_runtime` and never consulted `node.source`; this fix only changes the sqlite
branch's source of truth.

Run: `python tests/test_connect_declared_source.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

HERE = os.path.dirname(os.path.abspath(__file__))
DECLARED_PATH = os.path.join(HERE, '_test_declared_source.sqlite')
CONSTRUCTOR_PATH = os.path.join(HERE, '_test_constructor_source.sqlite')

def _cleanup():
    for p in (DECLARED_PATH, CONSTRUCTOR_PATH):
        if os.path.exists(p):
            try:
                os.remove(p)
            except PermissionError:
                pass

_cleanup()

# ── (a) the declared env var wins over the interpreter's own constructor db_path ────
os.environ.pop('DATABASE_URL', None)
os.environ['MY_DECLARED_DB'] = DECLARED_PATH
SRC_A = 'connect db as sqlite from env.MY_DECLARED_DB\nshow "connected"\n'
prog_a = transform(_P.parse(SRC_A), SRC_A)
it_a = MohioInterpreter(db_path=CONSTRUCTOR_PATH)
it_a.run(prog_a)
if getattr(it_a, '_db', None) is not None and hasattr(it_a._db, 'conn'):
    it_a._db.conn.close()
check("(a) the declared env var's file was created",
      os.path.exists(DECLARED_PATH), DECLARED_PATH)
check("(a) the constructor's db_path was NOT used instead",
      not os.path.exists(CONSTRUCTOR_PATH), CONSTRUCTOR_PATH)
_cleanup()

# ── (b) regression: an unset/empty declared var falls back to the constructor's db_path ──
os.environ.pop('MY_DECLARED_DB', None)
SRC_B = 'connect db as sqlite from env.MY_DECLARED_DB\nshow "connected"\n'
prog_b = transform(_P.parse(SRC_B), SRC_B)
it_b = MohioInterpreter(db_path=CONSTRUCTOR_PATH)
it_b.run(prog_b)
if getattr(it_b, '_db', None) is not None and hasattr(it_b._db, 'conn'):
    it_b._db.conn.close()
check("(b) regression: unset declared var falls back to the constructor's db_path",
      os.path.exists(CONSTRUCTOR_PATH), CONSTRUCTOR_PATH)
_cleanup()

# ── (c) ConnectDecl.source is a real EnvRef, not None (the root-cause bug directly) ──
SRC_C = 'connect db as sqlite from env.SOME_VAR\n'
prog_c = transform(_P.parse(SRC_C), SRC_C)
node_c = prog_c.statements[0]
from mohio_ast import EnvRef
check("(c) ConnectDecl.source is a real EnvRef node, not None",
      isinstance(node_c.source, EnvRef), type(node_c.source).__name__)
check("(c) the EnvRef carries the right key",
      getattr(node_c.source, 'key', None) == 'SOME_VAR', getattr(node_c.source, 'key', None))

# ── (d) regression: secret.X source also builds a real SecretRef, not None ───────────
SRC_D = 'connect db as sqlite from secret.SOME_SECRET\n'
prog_d = transform(_P.parse(SRC_D), SRC_D)
node_d = prog_d.statements[0]
from mohio_ast import SecretRef
check("(d) regression: secret.X source builds a real SecretRef node too",
      isinstance(node_d.source, SecretRef), type(node_d.source).__name__)

# ── (e) regression: postgres/mysql/mongo drivers are unaffected by the fix ──────────
# They already read os.environ directly inside _make_db_runtime; the fix only changes
# the sqlite branch. Confirm a postgres declaration still resolves DATABASE_URL itself,
# not through node.source, and that node.source being real now doesn't change that path.
os.environ['DATABASE_URL'] = 'postgres://host/db'
it_e = MohioInterpreter()
target = it_e._connection_target('postgres')
check("(e) regression: postgres target resolution still reads DATABASE_URL directly",
      target == ('postgres', 'postgres://host/db'), target)
os.environ.pop('DATABASE_URL', None)

_cleanup()
print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
