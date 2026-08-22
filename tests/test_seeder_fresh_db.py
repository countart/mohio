# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SEEDER-FRESH-DB (2026-08-20): seed_postgres.py works against a genuinely EMPTY database,
and is idempotent when re-run.

TWO BUGS, both deploy-blocking for any new environment, both proven live on Postgres 18.

  1. FRESH-DB SEEDING DID NOT WORK AT ALL. `upsert_rows` emitted `ON CONFLICT ("{pk}")`, which
     hard-requires a UNIQUE or exclusion constraint on that column, while `ensure_table` in the
     same file creates every column as plain TEXT with no constraint of any kind. So seeding an
     empty database crashed on the FIRST table, `rooms` (pk=`id`):
         psycopg2.errors.InvalidColumnReference: there is no unique or exclusion constraint
         matching the ON CONFLICT specification
     This was first filed as affecting only the tables whose pk_map entry is a non-`id` column.
     That was wrong and the correction matters: NO table gets a constraint, so EVERY table was
     affected and standing up a new environment was impossible. It only ever appeared to work
     against a database whose tables already carried constraints from some earlier life.

  2. RE-SEEDING DUPLICATED THE KEYLESS TABLES. Four seeded tables carry no `id` and no pk_map
     entry -- `exits`, `verb_aliases`, `item_aliases`, `flags`. Those took a plain INSERT, so
     every re-run appended the whole table again: exits went 340 -> 680 silently, and the
     duplicates then fed the game's own lookups. The file documents itself as re-runnable ("Run
     once after adding the Postgres service, or after schema changes"), so idempotence is part of
     the contract, not a nicety. This one was invisible before fix 1, because a fresh database
     never got far enough to re-run.

THE FIX, both cases: the shape already ruled for the runtime's own upsert
(T1-UPSERT-NO-CONSTRAINT, Option A) and used by `save_if_not_exists` long before that -- UPDATE
the matching row, else INSERT guarded by `WHERE NOT EXISTS` in one statement. Correct with or
without a constraint, so a table that DOES have a unique on its key keeps working unchanged. For
a keyless table there is nothing to match on but the row itself, so the whole row is the key.

VERIFIED LIVE end to end (2026-08-20), not just unit-shaped: a throwaway Postgres 18 dropped to an
empty schema, seeded from empty (9 tables, 786 rows), seeded a SECOND time with every count
unchanged, then `mio serve tests/zork/index.mho` answered a real cookieless HTTP request against
that seeder-built database, and probe7's A/B/C/D all passed on it.

COVERAGE: the always-on cases below assert the SQL shape through a recording cursor, so CI catches
a regression with no Postgres present. The live case runs the real seeder against a real server
when MOHIO_TEST_PG_URL is set, same skip convention as tests/test_audit_chain_postgres.py.

Run: `python tests/test_seeder_fresh_db.py`.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def _load_upsert_rows():
    """seed_postgres.py runs its work at import (connect, load json, seed), so it cannot simply be
    imported here. Lift the identity source and `upsert_rows` out of it instead -- a deliberate,
    labelled unit hook, paired with the live end-to-end case at the bottom of this file.

    TABLE_IDENTITY/identity_for come along too (T1-SEEDER-SCHEMA-CORRECTNESS): the upsert derives
    its match columns from the SAME identity the schema is built from, so lifting the function
    without them would exercise something the seeder never runs."""
    src = open(os.path.join(ROOT, 'seed_postgres.py'), encoding='utf-8').read()
    ns = {}
    i_start = src.index('TABLE_IDENTITY = {')
    i_end = src.index('def ensure_table(', i_start)
    exec(compile(src[i_start:i_end], 'seed_postgres_identity', 'exec'), ns)
    start = src.index('def upsert_rows(')
    end = src.index(chr(10) + '# ', start)
    exec(compile(src[start:end], 'seed_postgres_upsert', 'exec'), ns)
    return ns['upsert_rows'], ns['TABLE_IDENTITY'], ns['identity_for']


upsert_rows, TABLE_IDENTITY, identity_for = _load_upsert_rows()


class _RecordingCursor:
    """Records statements; reports 0 rows matched so the INSERT branch is always the one taken."""
    def __init__(self): self.sql = []; self.rowcount = 0
    def execute(self, sql, params=None): self.sql.append(' '.join(sql.split()))
    def joined(self): return ' | '.join(self.sql)


# -- the crash: no ON CONFLICT may be emitted, for a keyed table or any other ----------------
cur = _RecordingCursor()
upsert_rows(cur, 'rooms', [{'id': 'west', 'name': 'West of House'}])
j = cur.joined()
check("keyed table: ON CONFLICT is gone (it required a constraint no seeded table has)",
      'ON CONFLICT' not in j.upper(), j)
check("keyed table: the insert is guarded by WHERE NOT EXISTS",
      'WHERE NOT EXISTS' in j.upper(), j)
check("keyed table: an UPDATE is attempted first (so a re-seed refreshes rather than skips)",
      j.upper().startswith('UPDATE'), j)
check("keyed table: the guard matches on the key column",
      '"id" = %s' in j, j)

# A non-`id` key (saved_games/sessions use session_id) must behave identically.
cur = _RecordingCursor()
upsert_rows(cur, 'saved_games', [{'session_id': 's1', 'score': '10'}])
j = cur.joined()
check("non-id key: same constraint-free shape, keyed on session_id",
      'ON CONFLICT' not in j.upper() and '"session_id" = %s' in j, j)

# -- the duplication: a KEYLESS table must guard on the whole row ----------------------------
cur = _RecordingCursor()
upsert_rows(cur, 'exits', [{'room_id': 'west', 'direction': 'north', 'dest': 'north_of_house'}])
j = cur.joined()
check("exits: no longer a bare INSERT (that appended the table on every re-run)",
      'WHERE NOT EXISTS' in j.upper(), j)
# T1-SEEDER-SCHEMA-CORRECTNESS re-pointed this: exits now has a DECLARED identity
# (room_id, direction) taken from ZORK_FULL_CREATE_AND_SEED.sql, so the guard matches on that
# rather than on the whole row. Same protection, now matching the real schema.
check("exits: the guard matches its declared identity (room_id, direction)",
      '"room_id" = %s' in j and '"direction" = %s' in j, j)

# A single-column keyless table still guards, rather than falling back to a bare insert.
cur = _RecordingCursor()
upsert_rows(cur, 'verb_aliases', [{'alias': 'grab', 'canonical': 'take'}])
j = cur.joined()
check("verb_aliases: guarded on its declared identity (alias)",
      'WHERE NOT EXISTS' in j.upper() and '"alias" = %s' in j, j)


# -- LIVE: the real seeder against a real, genuinely empty Postgres --------------------------
_URL = os.environ.get('MOHIO_TEST_PG_URL')
if not _URL:
    print("  [SKIP] live: set MOHIO_TEST_PG_URL to run the real fresh-database seed")
else:
    import subprocess
    try:
        import psycopg2
        conn = psycopg2.connect(_URL); conn.autocommit = True
        c = conn.cursor()
        # Evict any other connection to this database FIRST. `DROP SCHEMA ... CASCADE` blocks
        # indefinitely behind a lock otherwise, and a developer with a `mio serve` pointed at the
        # same throwaway database is the normal case, not an unusual one -- without this the test
        # simply hangs with no output, which is what it did the first time it was run that way.
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = current_database() AND pid <> pg_backend_pid()")
        c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
        c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        empty_before = c.fetchone()[0]
        check("live: the database really is empty before seeding", empty_before == 0, empty_before)

        env = dict(os.environ, DATABASE_URL=_URL)
        r1 = subprocess.run([sys.executable, 'seed_postgres.py'], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=600)
        check("live: seeding an EMPTY database succeeds (it used to crash on the first table)",
              r1.returncode == 0, (r1.stdout or '')[-400:] + (r1.stderr or '')[-400:])

        def counts():
            out = {}
            for t in ('rooms', 'items', 'puzzles', 'exits', 'verb_aliases', 'item_aliases'):
                c.execute(f'SELECT count(*) FROM "{t}"')
                out[t] = c.fetchone()[0]
            return out

        first = counts()
        check("live: every table loaded with rows", all(v > 0 for v in first.values()), first)

        r2 = subprocess.run([sys.executable, 'seed_postgres.py'], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=600)
        check("live: a SECOND seed run also succeeds", r2.returncode == 0,
              (r2.stderr or '')[-400:])
        second = counts()
        check("live: re-seeding is IDEMPOTENT -- no table grew (exits used to go 340 -> 680)",
              second == first, f"before={first} after={second}")
        c.close(); conn.close()
    except Exception as e:
        check("live: fresh-database seed", False, f"{type(e).__name__}: {e}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
