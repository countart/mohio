# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SEEDER-SCHEMA-CORRECTNESS (2026-08-20): the seeder creates each table with the constraint
that table's REAL row identity requires -- not a bare `id` key, and not no key at all.

THE PATTERN THIS ENDS. Three times in two sessions a table was created with a shape that did not
match how the app actually identifies a row:
  * saved_games needed UNIQUE(session_id), not id            (fixed 88d6fbf)
  * items needs the composite (session_id, id)               (this item)
  * trophy_case has the SAME composite shape and was still declared as bare `item_id` here
    (found by the audit below, before it could become the fourth instance)

WHY items IS THE SHARP CASE. Every session clones its own copy of every item from the `__world__`
template, and the clone KEEPS the template's id -- `lantern` is `lantern` in every player's world,
differentiated only by session_id. A bare PRIMARY KEY (id) therefore breaks the very first clone,
against the template row itself. Reproduced live on Postgres 18 before the fix:

    sql.error: Raw sql failed: duplicate key value violates unique constraint "items_pkey"
    DETAIL:  Key (id)=(lantern) already exists.

With UNIQUE (session_id, id) three fresh players each cloned a full 166-item world and four rows
legitimately share id='lantern' (three players plus __world__).

THE ROOT CAUSE, and why this is a class fix rather than an items fix. Row identity was declared in
TWO disconnected places that could not agree: `ensure_table` created no constraint at all, while a
separate `pk_map` told the upsert what to match on. Nothing tied either to what the table actually
is. There is now ONE declaration -- `TABLE_IDENTITY` -- that drives BOTH the constraint the table
is created with AND the columns an upsert matches on, so the two cannot drift apart again. Tables
it does not name fall back to a DERIVED identity (a table carrying both session_id and id is
session-scoped by construction), rather than to a guess.

SOURCE OF TRUTH: `ZORK_FULL_CREATE_AND_SEED.sql`, which declares the correct constraint for every
table. The map mirrors that file; it was not designed here. All 12 tables were compared
table-by-table against it after a real fresh seed, and all 12 match.

THE OTHER HALF OF THE CLASS, NOT FIXED HERE (stated, not buried): the app's own runtime
auto-create, `_col_defs`/`ensure_table` in mohio_interpreter.py, ALWAYS emits `"id" ... PRIMARY
KEY` for any table it creates fresh, and only adds a composite when the program declared one via
`save ... unless a, b exists`. That is the same wrong-shape generator, one layer down. Seeding
every table correctly means the runtime never gets the chance to invent a schema for these tables
-- which is exactly the strategy ZORK_FULL_CREATE_AND_SEED.sql's own header describes -- but any
table the seeder does NOT pre-create is still exposed. Left for its own ruling.

COVERAGE: the always-on cases assert the identity map and the generated DDL with no Postgres
present, so CI catches a regression. The live case does a real fresh seed and a real multi-session
clone when MOHIO_TEST_PG_URL is set (same skip convention as tests/test_audit_chain_postgres.py).

Run: `python tests/test_seeder_schema_correctness.py`.
"""
import os
import re
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


def _load():
    """Lift the identity source and ensure_table out of seed_postgres.py -- it does its work at
    import, so it cannot be imported. Labelled unit hook, paired with the live case below."""
    src = open(os.path.join(ROOT, 'seed_postgres.py'), encoding='utf-8').read()
    ns = {}
    i0 = src.index('TABLE_IDENTITY = {')
    i1 = src.index('def upsert_rows(', i0)
    exec(compile(src[i0:i1], 'seed_identity_and_ensure', 'exec'), ns)
    return ns

_ns = _load()
TABLE_IDENTITY = _ns['TABLE_IDENTITY']
identity_for = _ns['identity_for']
ensure_table = _ns['ensure_table']


# -- The identity map is the authoritative shape, per table --------------------------------
EXPECTED = {
    'rooms':          ('id',),
    'items':          ('session_id', 'id'),
    'puzzles':        ('id',),
    'exits':          ('room_id', 'direction'),
    'item_aliases':   ('alias',),
    'verb_aliases':   ('alias',),
    'ejection_rooms': ('id',),
    'saved_games':    ('session_id',),
    'sessions':       ('session_id',),
    'flags':          ('session_id', 'flag_name'),
    'ai_cache':       ('command', 'room'),
    'trophy_case':    ('session_id', 'item_id'),
}
for t, want in EXPECTED.items():
    check(f"identity declared for {t}: {', '.join(want)}",
          TABLE_IDENTITY.get(t) == want, TABLE_IDENTITY.get(t))

# The three that carry the actual history -- called out so a regression names itself.
check("items identity is the COMPOSITE, never a bare id (the clone-killing case)",
      TABLE_IDENTITY['items'] == ('session_id', 'id'), TABLE_IDENTITY['items'])
check("trophy_case identity is the composite too (was declared bare item_id)",
      TABLE_IDENTITY['trophy_case'] == ('session_id', 'item_id'), TABLE_IDENTITY['trophy_case'])
check("saved_games identity stays session_id (the 88d6fbf fix is not regressed)",
      TABLE_IDENTITY['saved_games'] == ('session_id',), TABLE_IDENTITY['saved_games'])


# -- Derivation, for a table the map does not name -----------------------------------------
check("derived: session_id + id -> the composite (session-scoped by construction)",
      identity_for('brand_new', ['session_id', 'id', 'name']) == ('session_id', 'id'))
check("derived: id alone -> id", identity_for('brand_new', ['id', 'name']) == ('id',))
check("derived: session_id alone -> session_id",
      identity_for('brand_new', ['session_id', 'x']) == ('session_id',))
check("derived: nothing key-like -> the whole row is the identity",
      identity_for('brand_new', ['a', 'b']) == ('a', 'b'))


# -- The generated DDL, captured without a database -----------------------------------------
class _Cur:
    def __init__(self, existing_unique=False):
        self.sql = []; self._existing = existing_unique
    def execute(self, sql, params=None): self.sql.append(' '.join(sql.split()))
    def fetchone(self): return (1,) if self._existing else None

def ddl(table, cols, existing_unique=False):
    c = _Cur(existing_unique)
    ensure_table(c, table, cols)
    return ' | '.join(c.sql)

items_cols = ['id', 'name', 'location', 'session_id']
d = ddl('items', items_cols)
check("items DDL declares UNIQUE (session_id, id)",
      'UNIQUE ("session_id", "id")' in d, d)
check("items DDL puts NO primary key on id (that is what broke the clone)",
      'PRIMARY KEY' not in d.upper(), d)

d = ddl('rooms', ['id', 'name'])
check("rooms DDL uses a real PRIMARY KEY on id (single-column identity)",
      '"id" TEXT PRIMARY KEY' in d, d)

d = ddl('trophy_case', ['session_id', 'item_id', 'scored_at'])
check("trophy_case DDL declares UNIQUE (session_id, item_id), no bare key",
      'UNIQUE ("session_id", "item_id")' in d and 'PRIMARY KEY' not in d.upper(), d)

d = ddl('ai_cache', ['command', 'room', 'response'])
check("ai_cache DDL declares UNIQUE (command, room) -- it used not to be created at all",
      'UNIQUE ("command", "room")' in d, d)

# The repair path: an existing table with no uniqueness gets an index; one that already has the
# right uniqueness must NOT get a second, redundant index over the same columns.
d = ddl('items', items_cols, existing_unique=False)
check("repair: a pre-existing table missing its uniqueness gets a unique index",
      'CREATE UNIQUE INDEX' in d.upper(), d)
d = ddl('items', items_cols, existing_unique=True)
check("no redundancy: a table that already has the uniqueness gets NO second index",
      'CREATE UNIQUE INDEX' not in d.upper(), d)


# -- LIVE: real fresh seed, real multi-session clone -----------------------------------------
_URL = os.environ.get('MOHIO_TEST_PG_URL')
if not _URL:
    print("  [SKIP] live: set MOHIO_TEST_PG_URL for the real fresh-seed + clone case")
else:
    import subprocess
    try:
        import psycopg2
        conn = psycopg2.connect(_URL); conn.autocommit = True
        c = conn.cursor()
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = current_database() AND pid <> pg_backend_pid()")
        c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')

        r = subprocess.run([sys.executable, 'seed_postgres.py'], cwd=ROOT,
                           env=dict(os.environ, DATABASE_URL=_URL),
                           capture_output=True, text=True, timeout=900)
        check("live: a genuinely empty database seeds without error",
              r.returncode == 0, (r.stderr or '')[-400:])

        # Every table's real uniqueness, straight from the catalogue.
        c.execute("""
            SELECT c.relname, array_agg(a.attname::text ORDER BY k.ord)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public'
              JOIN pg_index i ON i.indrelid=c.oid AND i.indisunique
              JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
              JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
             GROUP BY c.relname, i.indexrelid""")
        actual = {}
        for t, cols in c.fetchall():
            actual.setdefault(t, set()).add(tuple(cols))

        for t, want in EXPECTED.items():
            check(f"live schema: {t} is unique on ({', '.join(want)})",
                  want in actual.get(t, set()), sorted(actual.get(t, set())))

        # The clone that was dying: many sessions, all sharing the template's ids.
        c.execute("SELECT count(*) FROM items WHERE session_id='__world__'")
        world = c.fetchone()[0]
        check("live: the __world__ template loaded", world > 0, world)
        for sess in ('sessA', 'sessB', 'sessC'):
            c.execute("""INSERT INTO items (session_id, id, name, location)
                         SELECT %s, id, name, location FROM items WHERE session_id='__world__'""",
                      (sess,))
        c.execute("SELECT count(DISTINCT session_id) FROM items")
        check("live: three sessions cloned the world alongside the template (4 owners)",
              c.fetchone()[0] == 4)
        c.execute("SELECT count(*) FROM items WHERE id=(SELECT id FROM items "
                  "WHERE session_id='__world__' LIMIT 1)")
        check("live: one item id legitimately exists in every session (the composite's whole point)",
              c.fetchone()[0] == 4)
        # And the composite still forbids a genuine duplicate within one session.
        try:
            c.execute("""INSERT INTO items (session_id, id, name, location)
                         SELECT 'sessA', id, name, location FROM items
                          WHERE session_id='__world__' LIMIT 1""")
            check("live: a TRUE duplicate (same session, same id) is still refused", False,
                  "the insert was accepted")
        except Exception as e:
            check("live: a TRUE duplicate (same session, same id) is still refused",
                  'unique' in str(e).lower() or 'duplicate' in str(e).lower(), str(e)[:160])
        c.close(); conn.close()
    except Exception as e:
        check("live: fresh seed + clone", False, f"{type(e).__name__}: {e}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
