# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`save ... unless a, b exists` -- composite dedupe key, and the pure-existence upsert fix.

Zork's per-session flags are identified by (session_id, flag_name) TOGETHER; neither column
alone identifies a row. Before this:
  - `save to db.flags` had NO conflict target of any kind (all four backends issue a bare
    INSERT). On a table with the correct UNIQUE(session_id, flag_name) -- i.e. a CORRECTLY
    migrated database -- re-setting a flag during normal play raised
    `UNIQUE constraint failed` and killed the request. On a table without the constraint it
    silently accumulated duplicate rows.
  - `unless <field> exists` existed but was grammatically SINGLE-field, so it could not express
    the key, and it was a non-atomic SELECT-then-INSERT (TOCTOU).
  - A composite `upsert` whose fields are ALL match keys (pure existence, no payload column)
    hit the same wall on SQLite: the fallback built `UPDATE t SET  WHERE ...` with an empty
    SET, errored, swallowed it, returned 0, and fell through to a plain INSERT that violated
    the constraint. Postgres was already correct here (ON CONFLICT ... DO NOTHING).

Now `unless` takes a name_list, and the insert goes through `save_if_not_exists` --
ONE atomic `INSERT ... SELECT ... WHERE NOT EXISTS`. Deliberately not `ON CONFLICT`: that
hard-requires a matching UNIQUE constraint, which Mohio's own auto-created tables never have
on a non-id column (verified: SQLite raises "ON CONFLICT clause does not match any PRIMARY KEY
or UNIQUE constraint"), so ON CONFLICT would break on exactly the tables Mohio makes.

Run: `python tests/test_save_composite_dedupe.py`.
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

def run(src, db_path=None):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(db_path=db_path) if db_path else MohioInterpreter()
    it.run_declarations(prog)
    it.shown = []
    try:
        r = it.run(prog)
    except Exception as e:
        return 'FAILLOUD: ' + str(e)
    finally:
        # Windows will not unlink a sqlite file whose handle is still open.
        try:
            db = getattr(it, '_db', None)
            if db is not None and hasattr(db, 'close'):
                db.close()
        except Exception:
            pass
    if isinstance(r, dict) and r.get('status') == 500:
        return 'FAILLOUD: ' + str(r.get('body'))
    return it.shown

def seeded_db(ddl):
    """A CORRECTLY migrated database -- the case that used to break."""
    path = tempfile.mktemp(suffix='.db')
    c = sqlite3.connect(path); c.execute(ddl); c.commit(); c.close()
    return path

FLAG_DDL = ('CREATE TABLE flags ("id" TEXT PRIMARY KEY, "session_id" TEXT, '
            '"flag_name" TEXT, UNIQUE(session_id, flag_name))')

# ── 1. Zork's exact pattern on a CORRECTLY SEEDED db (the live blocker) ────────────────────
ZORK = (CONN +
        'save to db.flags unless session_id, flag_name exists\n'
        '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n'
        'save to db.flags unless session_id, flag_name exists\n'
        '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n'
        'find f in db.flags\nfind: done\nshow "rows={{ f.count }}"\n')
db = seeded_db(FLAG_DDL)
out = run(ZORK, db_path=db)
check("seeded DB (UNIQUE session_id,flag_name): repeated flag-set succeeds, ONE row "
      "(was: UNIQUE constraint failed, request killed)", out == ['rows=1'], str(out))
os.unlink(db)

# ── 2. Same program on a table with NO constraint -- must also be one row, not duplicates ──
out = run(ZORK)
check("unconstrained table: repeated flag-set is still ONE row (was: silent duplicates)",
      out == ['rows=1'], str(out))

# ── 3. The composite key really is COMPOSITE: differing in either column is a DIFFERENT row ─
DIFFERENT = (CONN +
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n'
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "lamp_lit"\nsave: done\n'      # same session, other flag
    'save to db.flags unless session_id, flag_name exists\n'
    '    session_id "s2"\n    flag_name "troll_dead"\nsave: done\n'    # other session, same flag
    'find f in db.flags\nfind: done\nshow "rows={{ f.count }}"\n')
out = run(DIFFERENT)
check("composite key: rows differing in EITHER column are distinct (3 rows, not deduped to 1)",
      out == ['rows=3'], str(out))

# ── 4. Single-field `unless exists` unchanged (regression guard) ───────────────────────────
SINGLE = (CONN +
    'save to db.flags unless flag_name exists\n'
    '    session_id "s1"\n    flag_name "troll_dead"\nsave: done\n'
    'save to db.flags unless flag_name exists\n'
    '    session_id "s9"\n    flag_name "troll_dead"\nsave: done\n'    # different session, SAME flag
    'find f in db.flags\nfind: done\nshow "rows={{ f.count }}"\n')
out = run(SINGLE)
check("single-field `unless exists` unaffected: dedupes on that one column alone (1 row)",
      out == ['rows=1'], str(out))

# ── 5. A plain save with no `unless` still inserts every time (regression guard) ───────────
PLAIN = (CONN +
    'save to db.flags\n    session_id "s1"\n    flag_name "x"\nsave: done\n'
    'save to db.flags\n    session_id "s1"\n    flag_name "x"\nsave: done\n'
    'find f in db.flags\nfind: done\nshow "rows={{ f.count }}"\n')
out = run(PLAIN)
check("plain save (no unless) still inserts unconditionally -- 2 rows (regression guard)",
      out == ['rows=2'], str(out))

# ── 6. `unless` naming a column this save never writes fails loud (no silent degrade) ──────
BAD = (CONN + 'save to db.flags unless nosuchcol exists\n    session_id "s1"\nsave: done\n')
out = run(BAD)
check("unless naming a column the save does not write -> fails loud, not a silent plain insert",
      isinstance(out, str) and out.startswith('FAILLOUD') and 'nothing to match' in out.lower(),
      str(out))

# ── 7. The folded-in fix: PURE-EXISTENCE composite upsert on a SEEDED db ───────────────────
#     (every field is a match key -- no payload column. Was: empty SET -> swallowed error ->
#      plain INSERT -> UNIQUE violation. Postgres was already correct via DO NOTHING.)
PURE = (CONN +
    'upsert db.flags\n    match session_id to "s1", flag_name to "troll_dead"\nupsert: done\n'
    'upsert db.flags\n    match session_id to "s1", flag_name to "troll_dead"\nupsert: done\n'
    'find f in db.flags\nfind: done\nshow "rows={{ f.count }}"\n')
db = seeded_db(FLAG_DDL)
out = run(PURE, db_path=db)
check("pure-existence composite upsert on a seeded DB: succeeds, ONE row "
      "(was: UNIQUE constraint failed on SQLite while Postgres was fine)",
      out == ['rows=1'], str(out))
os.unlink(db)

# ── 8. Composite upsert WITH a payload column still updates in place (regression guard) ────
PAYLOAD_DDL = ('CREATE TABLE flags ("id" TEXT PRIMARY KEY, "session_id" TEXT, '
               '"flag_name" TEXT, "note" TEXT, UNIQUE(session_id, flag_name))')
WITH_PAYLOAD = (CONN +
    'upsert db.flags\n    match session_id to "s1", flag_name to "troll_dead"\n'
    '    note "first"\nupsert: done\n'
    'upsert db.flags\n    match session_id to "s1", flag_name to "troll_dead"\n'
    '    note "second"\nupsert: done\n'
    'find f in db.flags\nfind: done\nshow "rows={{ f.count }} note={{ f.first.note }}"\n')
db = seeded_db(PAYLOAD_DDL)
out = run(WITH_PAYLOAD, db_path=db)
check("composite upsert WITH a payload column still updates in place (1 row, note=second)",
      out == ['rows=1 note=second'], str(out))
os.unlink(db)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
