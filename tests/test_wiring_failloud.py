#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for the "no silent no-op" wiring guarantees:

  1. The interpreter dispatch FAILS LOUD on any statement-level node that has no
     executor (previously it silently returned None, hiding unwired constructs).
  2. cm.retain / cm.report / cm.notify emit a `mio check` WARNING (declared but
     not executed -- compliance must not silently no-op).
  3. notify emits a `mio check` WARNING.
  4. A normal `sector: demo_low` program (no explicit cm.* / notify) does NOT
     get spammed with those warnings (they fire only on direct authoring use).
  5. verify token remains fail-loud (auth must never silently pass).
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
from mohio_transformer import validate

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def warns(src):
    return [w.message for w in validate(P.parse(src), source=src, filename='t.mho').warnings]

# 1. dispatch fails loud on an unwired node
class _Unwired:  # a node type with no _exec_ handler
    pass
it = MohioInterpreter()
loud = False
try:
    it._exec(_Unwired(), it.global_ctx if hasattr(it, 'global_ctx') else None)
except MohioRuntimeError as e:
    loud = 'No executor' in str(e) or 'not executable' in str(e)
except Exception:
    loud = False
check("dispatch fails loud on a node with no executor", loud)

# 2. cm.* compliance actions warn
check("cm.report warns (declared but not executed)",
      any('not yet' in w.lower() or 'compliance action' in w.lower()
          for w in warns('cm.report "CTR" for transaction\n')))

# 3. notify warns
check("notify warns (declared but not executed)",
      any('notify' in w.lower() and 'not yet' in w.lower()
          for w in warns('notify admin with breach\n')))

# 4. no spam on a normal sector program
spam = [w for w in warns('sector: demo_low\n'
                         'connect db as sqlite from env.DATABASE_URL\n'
                         'show "ok"\n')
        if 'compliance action' in w.lower() or ('notify' in w.lower() and 'not yet' in w.lower())]
check("normal sector program is not spammed with cm.*/notify warnings", not spam)

# 5. bare `verify token` must fail loud -- now at TRANSFORM time so `mio check` catches it
from mohio_transformer_ast import transform
verify_loud = False
try:
    src = 'verify token\n'
    prog = transform(P.parse(src), src)
    it2 = MohioInterpreter(); it2.run_declarations(prog); it2.run(prog)
except Exception as e:
    verify_loud = 'verify' in str(e).lower() and 'keyword' in str(e).lower()
# Bare `verify token` must fail loud -- auth must never silently pass.
check("bare verify token fails loud (at check)", verify_loud)

# 5b. `connect ai` must fail loud at TRANSFORM time (ai reserved, connections implicit)
connect_ai_loud = False
try:
    src = 'connect ai\nshow "x"\n'
    prog = transform(P.parse(src), src)
    it3 = MohioInterpreter(); it3.run_declarations(prog); it3.run(prog)
except Exception as e:
    connect_ai_loud = 'connect' in str(e).lower() and ('keyword' in str(e).lower() or 'never valid' in str(e).lower())
check("connect ai fails loud (at check)", connect_ai_loud)

# 5c. inline `find ... then` (no closer) must fail loud pointing to the block form
find_inline_loud = False
try:
    src = 'find xs in db.nums then show it.count\n'
    prog = transform(P.parse(src), src)
    it4 = MohioInterpreter(); it4.run_declarations(prog); it4.run(prog)
except Exception as e:
    find_inline_loud = 'find' in str(e).lower() and 'find: done' in str(e).lower()
check("inline find-then fails loud with block-form guidance (at check)", find_inline_loud)

# 6. cm.purge: warns at check AND fails loud at runtime (right-to-be-forgotten
#    must never silently no-op -- worse than retain/report, hence fail-loud not no-op)
_purge = 'cm.purge member.id\n    reason "GDPR Article 17"\ncm.purge: done\n'
check("cm.purge warns at check (not yet executed)",
      any('purge' in w.lower() and ('not yet' in w.lower() or 'no data' in w.lower())
          for w in warns(_purge)))
purge_loud = False
_purge_nr = 'cm.purge member.id\ncm.purge: done\n'   # no reason -> must fail loud
try:
    from mohio_transformer_ast import transform as _tf
    _prog = _tf(P.parse(_purge_nr), _purge_nr)
    _it = MohioInterpreter(); _it.run_declarations(_prog); _it.run(_prog)
except MohioRuntimeError as e:
    purge_loud = 'cm.purge' in str(e).lower() and 'reason' in str(e).lower()
# The value form WITH a reason records an erasure-request audit and returns (by
# design; the developer writes the delete, the cascade is the paid tier). The
# fail-loud is the reason guard.
check("cm.purge fails loud without a reason (value form is record+audit by design)", purge_loud)

# 7. Linkage audit: save all / remove all build REAL nodes (not raw Trees) and
#    reach their executors; dead-code constructs fail loud naming the construct.
from mohio_interpreter import DbRuntime, Context
from mohio_transformer_ast import transform as _tf2
from lark import Tree as _LarkTree
_sa = _tf2(P.parse('save all to db.people from batch\nsave: done\n'), 's').statements[0]
check("save all builds a real SaveAllBlock (not a raw Tree)", type(_sa).__name__ == 'SaveAllBlock')
_ra = _tf2(P.parse('remove.all from db.temp\nremove.all: done\n'), 's').statements[0]
check("remove all builds a real RemoveAllBlock (not a raw Tree)", type(_ra).__name__ == 'RemoveAllBlock')
# remove all actually deletes
_db = DbRuntime(':memory:'); _db.conn.execute("CREATE TABLE temp (x TEXT)")
_db.conn.executemany("INSERT INTO temp VALUES (?)", [("a",), ("b",)]); _db.conn.commit()
_it = MohioInterpreter(); _it._db = _db
_ctx = Context(); _ctx.set_connection('db', _db); _it._exec(_ra, _ctx)
check("remove all clears the table", _db.conn.execute("SELECT COUNT(*) FROM temp").fetchone()[0] == 0)
# retrieve.all / .one (retrieve_mod_block) is now WIRED -- it builds a real
# RetrieveBlock carrying the modifier (it used to be a dead rule that failed
# loud). The generic dead-code fail-loud guarantee is covered by check #1 above.
_rmod = _tf2(P.parse('retrieve.all m from db.members\nretrieve.all: done\n'), 's').statements[0]
check("retrieve.all builds a real RetrieveBlock (not a raw Tree)", type(_rmod).__name__ == 'RetrieveBlock')
check("retrieve.all carries modifier='all'", getattr(_rmod, 'modifier', None) == 'all')

# 8. run async / wait for fail loud at COMPILE. They were fail-late traps:
#    grammar + AST + a dead _exec existed but there was no transformer, so they
#    parsed + validated and then died at runtime as a generic "No executor"
#    (run async also ignored the task; wait for was a pure no-op). Now they raise
#    a clear, pointed compile error. Reverse when true async is wired.
from mohio_transformer_ast import transform as _tf3, MohioCompileError as _CompileErr

def _compile_fails_loud(src, needle):
    try:
        _tf3(P.parse(src), src)
        return False
    except _CompileErr as e:
        return needle in str(e).lower()
    except Exception:
        return False

check("run async fails loud at compile (async not implemented)",
      _compile_fails_loud('run async send_email\n', 'run async'))
check("wait for fails loud at compile (async coordination not implemented)",
      _compile_fails_loud('wait for send_email\n', 'wait for'))

# 9. ai.override fails loud at runtime. A silent no-op would keep the AI's
#    original (possibly wrong) decision while the code reads as a human override
#    -- the wrong failure mode in a fraud / compliance setting.
override_loud = False
try:
    _oprog = _tf3(P.parse('ai.override decision with true\n'), 'o')
    _oit = MohioInterpreter(); _oit.run_declarations(_oprog); _oit.run(_oprog)
except MohioRuntimeError as e:
    override_loud = 'ai.override' in str(e).lower() and 'keep' in str(e).lower()
check("ai.override fails loud at runtime (does not silently keep AI decision)",
      override_loud)

# 10. Structural clauses that carry SEMANTICS (otherwise / or if / trailing
#     qualifier) fail loud if they ever reach the interpreter standalone -- a
#     parser/transformer leak -- rather than silently dropping a branch or
#     qualifier. (Closer is exempt: it carries no runtime semantics and
#     legitimately flows through block bodies.)
from types import SimpleNamespace as _NS
_iter = MohioInterpreter()
for _h, _label in [('_exec_OtherwiseClause', 'otherwise'),
                   ('_exec_OrIfClause', 'or if'),
                   ('_exec_TrailingQualifier', 'trailing qualifier')]:
    _loud = False
    try:
        getattr(_iter, _h)(_NS(body=[], condition=None), None)
    except MohioRuntimeError as e:
        _loud = 'internal' in str(e).lower()
    except Exception:
        _loud = False
    check(f"{_label} handler fails loud on leak (no silent pass)", _loud)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
