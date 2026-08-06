# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""3a: a declared `save ... unless a, b exists` CREATES the real composite UNIQUE constraint.

Ruled 2026-08-04. One source of truth: the key is stated once, at the write site, and the
schema follows from it -- there is no second declaration that can drift. Before this, Mohio's
auto-created tables never got a unique constraint on any non-id column (`_col_defs`), so a
clean-database deploy produced a schema that could not enforce the identity the program
declared, and the constraint had to be added by a manual ALTER outside Mohio (which is exactly
how Zork's production database came to differ from a fresh one).

Covers, per the ruling:
  - a clean-DB deploy using ONLY `save ... unless a, b exists` yields a table WITH the real
    composite UNIQUE(a, b) -- no manual schema step;
  - two sites declaring DIFFERENT keys for the same table FAIL LOUD naming both sites and both
    keys, rather than silently picking one;
  - a table with no `unless` declaration anywhere gets NO spurious constraint.

Run: `python tests/test_declared_unique_schema.py`.
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

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

CONN = 'connect db as sqlite from env.DATABASE_URL\n'

def run_on_clean_db(src):
    """Run against a genuinely EMPTY database file -- the clean-deploy case."""
    path = tempfile.mktemp(suffix='.db')
    # CONN declares `from env.DATABASE_URL`, and the connect-decl-source fix
    # (2026-08-05) now makes that declared source win over the constructor's
    # db_path= for a direct-Python caller. Point the env var at `path` too so
    # the declared source and the constructor agree on where to write --
    # otherwise the run lands in :memory: while this function inspects `path`.
    os.environ['DATABASE_URL'] = path
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(db_path=path)
    err = None
    try:
        it.run_declarations(prog)
        it.shown = []
        r = it.run(prog)
        if isinstance(r, dict) and r.get('status') == 500:
            err = 'FAILLOUD: ' + str(r.get('body'))
    except Exception as e:
        err = 'FAILLOUD: ' + str(e)
    finally:
        try:
            db = getattr(it, '_db', None)
            if db is not None and hasattr(db, 'close'):
                db.close()
        except Exception:
            pass
    schema = None
    if os.path.exists(path):
        c = sqlite3.connect(path); cur = c.cursor()
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='flags'")
        row = cur.fetchone()
        schema = row[0] if row else None
        c.close()
        try: os.unlink(path)
        except Exception: pass
    return err, schema, getattr(it, 'shown', [])

# ── 1. Clean-DB deploy: the declared key becomes a REAL composite UNIQUE ───────────────────
DECLARED = (CONN +
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n')
err, schema, _ = run_on_clean_db(DECLARED)
check("clean-DB deploy runs with no error", err is None, str(err))
check("clean-DB deploy CREATES the composite UNIQUE(session_id, flag_name) -- no manual ALTER",
      schema is not None and 'UNIQUE' in schema.upper()
      and 'session_id' in schema and 'flag_name' in schema, str(schema))

# ── 2. The created constraint is REAL: the database itself now rejects a duplicate ─────────
#     (proves it is an enforced constraint, not just text in the DDL)
path = tempfile.mktemp(suffix='.db')
os.environ['DATABASE_URL'] = path  # see note in run_on_clean_db above
prog = transform(_P.parse(DECLARED), DECLARED)
it = MohioInterpreter(db_path=path)
it.run_declarations(prog); it.run(prog)
try:
    db = getattr(it, '_db', None)
    if db is not None and hasattr(db, 'close'): db.close()
except Exception: pass
c = sqlite3.connect(path); cur = c.cursor()
rejected = False
try:
    cur.execute("INSERT INTO flags (session_id, flag_name) VALUES ('s1','troll_dead')")
    c.commit()
except sqlite3.IntegrityError:
    rejected = True
c.close()
try: os.unlink(path)
except Exception: pass
check("the constraint is ENFORCED by the database (a raw duplicate INSERT is rejected)",
      rejected, "raw duplicate insert was accepted -- constraint is not real")

# ── 3. No `unless` declaration anywhere -> NO spurious constraint ──────────────────────────
PLAIN = (CONN + 'save to db.flags\n    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n')
err, schema, _ = run_on_clean_db(PLAIN)
check("a table with no `unless` declaration gets NO constraint (no spurious UNIQUE)",
      schema is not None and 'UNIQUE' not in schema.upper(), str(schema))

# ── 4. Single-field `unless` does NOT create a composite constraint ────────────────────────
SINGLE = (CONN +
    'save to db.flags unless flag_name exists\n'
    '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n')
err, schema, _ = run_on_clean_db(SINGLE)
check("single-field `unless` creates no composite UNIQUE (unchanged behavior)",
      schema is not None and 'UNIQUE' not in schema.upper(), str(schema))

# ── 5. CONFLICT: two sites, same table, DIFFERENT keys -> fail loud naming BOTH ────────────
CONFLICT = (CONN +
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "a"\nsave: done\n'
    'save to db.flags unless session_id, other_col exists\n'
    '    session_id "s1"\n    other_col "b"\nsave: done\n')
err, schema, _ = run_on_clean_db(CONFLICT)
check("conflicting keys for one table FAIL LOUD (not silently resolved)",
      err is not None and 'conflicting' in err.lower(), str(err))
check("the conflict error names the TABLE", err is not None and 'flags' in err, str(err))
check("the conflict error names BOTH key sets (flag_name and other_col)",
      err is not None and 'flag_name' in err and 'other_col' in err, str(err))
check("the conflict error names BOTH sites (two line numbers)",
      err is not None and err.count('line ') >= 2, str(err))

# ── 6. The SAME key declared at two sites is NOT a conflict (order-insensitive) ────────────
SAME = (CONN +
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "a"\nsave: done\n'
    'save to db.flags unless flag_name, session_id exists\n'      # same columns, other order
    '    session_id "s1"\n    flag_name "b"\nsave: done\n')
err, schema, _ = run_on_clean_db(SAME)
check("the same key at two sites is NOT a conflict, even in a different order",
      err is None, str(err))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
