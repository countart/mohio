# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-AUDIT-COMPOSITE-KEY-IDENTITY (2026-08-21): a data-change audit record can always say WHICH
row changed, and says HOW that row is identified.

THE GAP. A save into a composite / id-less table recorded `record_id=None` (flagged as the open
boundary of the id-less-save fix, `7913fa4`). For a compliance-positioned release, an audit record
that cannot identify the row it describes fails at the one job it exists to do. `flags` is the live
example: UNIQUE (session_id, flag_name), no id, so every flag Zork sets audited to nothing.

WHAT A RECORD NOW CARRIES:
    composite   record_id "session_id=s9,flag_name=door_open"
                record_identity "composite(session_id,flag_name)"
    keyed       record_id "p1"                      <- unchanged, exactly as before
                record_identity "id"                <- additive only
    unknown     no record_id, record_identity "unknown"

`record_identity` is why a compliance reader is not left guessing why a record_id is not a bare id:
the record states its own identity shape, and names the columns to query by.

ONE IDENTITY SOURCE. The shape comes from `table_identity()`, which asks the DATABASE -- its
PRIMARY KEY, else its narrowest UNIQUE constraint. That is the same declaration
T1-SEEDER-SCHEMA-CORRECTNESS writes and the upsert matches on, so the audit cannot drift from the
schema, and it still works for a table the seeder never touched. Copying the seeder's
TABLE_IDENTITY map into the compiler was the obvious-looking alternative and is exactly what was
avoided: seed_postgres.py does its work at import and is a Zork seeding script, so the compiler
cannot depend on it, and a duplicate map is two truths waiting to disagree.

REDACTION -- the part that needed a decision, recorded here because it constrains the format.
`_audit_data_change`'s own contract is that the log holds field NAMES and a surrogate id, "never
the written values ... so a compliance audit trail can never become a second, unguarded copy of
the sensitive data it exists to protect." A natural composite key IS written data, so rendering it
runs straight at that guarantee. Any identity column tagged [phi]/[pii]/[pci] is therefore rendered
`col=<redacted>` -- the row stays identifiable by shape and by its untagged parts, and the tagged
value never lands.

Measured precisely rather than assumed, because the first version of this note overclaimed: a
tagged field is ALREADY encrypted by the time the audit sees it, so with redaction removed the
record_id renders `ssn=enc:v1:tyVKLUeo...`, the ciphertext, not the plaintext. Field encryption --
not this redaction -- is what keeps the plaintext out. Redaction earns its place for a different
reason: a ciphertext blob is useless as an identity (encryption need not be deterministic, so it
may not even match what is stored), and it bloats every record. The sentinel case below therefore
proves ENCRYPTION holds; the `<redacted>` assertion is what proves redaction, confirmed by
mutation.

UPDATE / REMOVE, and the boundary that remains. The sweep found that only `save` ever passed a
record_id at all: update, remove, upsert, save_all and modify recorded match column NAMES and a
count, never which row. They now record `record_identity`, so a reader knows the identity shape and
which columns to query -- but NOT the identity VALUES, because for those verbs the values are the
lookup values the audit contract explicitly refuses to store. Closing that fully means deciding
whether an update/remove may record the matched row's key, which is a compliance ruling and not
one this unit makes silently. Same reason a fully-tagged composite renders every part redacted
rather than being replaced by a one-way digest.

Real .mho through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD), on SQLite always and
on real Postgres when MOHIO_TEST_PG_URL is set. Verified live on Postgres 18 (2026-08-21).

Run: `python tests/test_audit_composite_identity.py`.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter, DbRuntime

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

SSN = "SENTINEL_SSN_123456789"


def run_sqlite(ddl, src):
    """Real source through the full pipeline, against a real SQLite schema whose identity is
    declared the way the seeder declares one."""
    db = DbRuntime(':memory:')
    for stmt in ddl:
        db.conn.execute(stmt)
    db.conn.commit()
    it = MohioInterpreter(); it._db = db
    prog = ast_transform(P.parse(src), src)
    it.run_declarations(prog); it.run(prog)
    return it

def entries(it):
    return [e for e in (getattr(it, '_audit_logs', {}) or {}).get('data_audit_log', [])]

def of(it, op, table):
    for e in entries(it):
        if e.get('operation') == op and e.get('table') == table:
            return e
    return {}

HEAD = 'connect db as sqlite from env.DATABASE_URL\n'


# -- COMPOSITE: the row is identified, and the record says how ------------------------------
it = run_sqlite(
    ['CREATE TABLE flags (session_id TEXT, flag_name TEXT, note TEXT, '
     'UNIQUE (session_id, flag_name))'],
    HEAD + 'shape Flag\n    session_id as text\n    flag_name as text\n'
           '    note as text [pii]\nshape: done\n'
           'save to db.flags\n    session_id "s9"\n    flag_name "door_open"\n    note "n"\n'
           'save: done\n')
e = of(it, 'save', 'flags')
check("composite save records the REAL identity, never None",
      e.get('record_id') == 'session_id=s9,flag_name=door_open', e)
check("composite save names its identity shape and columns",
      e.get('record_identity') == 'composite(session_id,flag_name)', e)

# -- KEYED: unchanged record_id, shape added additively --------------------------------------
it = run_sqlite(
    ['CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT, secret TEXT)'],
    HEAD + 'shape Person\n    id as text\n    name as text\n    secret as text [pii]\n'
           'shape: done\n'
           'save to db.people\n    id "p1"\n    name "Bo"\n    secret "x"\nsave: done\n')
e = of(it, 'save', 'people')
check("keyed save still records the bare id, format unchanged",
      e.get('record_id') == 'p1', e)
check("keyed save declares itself as id-identified", e.get('record_identity') == 'id', e)

# -- REDACTION: a tagged identity column must never reach the log ----------------------------
it = run_sqlite(
    ['CREATE TABLE patients (clinic_id TEXT, ssn TEXT, note TEXT, UNIQUE (clinic_id, ssn))'],
    HEAD + 'shape Patient\n    clinic_id as text\n    ssn as text [phi]\n    note as text\n'
           'shape: done\n'
           f'save to db.patients\n    clinic_id "c1"\n    ssn "{SSN}"\n    note "n"\nsave: done\n')
e = of(it, 'save', 'patients')
check("a TAGGED identity column is redacted, not rendered",
      e.get('record_id') == 'clinic_id=c1,ssn=<redacted>', e)
check("the row is still identifiable by its untagged part",
      'clinic_id=c1' in str(e.get('record_id')), e)
blob = json.dumps(entries(it), default=str)
# NB: this passes with or without redaction -- the field is encrypted before the audit runs, so
# it guards the ENCRYPTION invariant, not the redaction. The `<redacted>` check above is the one
# wired to redaction (mutation-proved). Kept because plaintext-in-the-audit-log is worth a
# standing guard wherever it can be cheaply asserted.
check("no tagged plaintext appears anywhere in the audit log (encryption invariant)",
      SSN not in blob, blob[:200])

# -- UNKNOWN: a table with no declared uniqueness says so rather than inventing one ----------
it = run_sqlite(
    ['CREATE TABLE loose (a TEXT, b TEXT)'],
    HEAD + 'shape Loose\n    a as text\n    b as text [pii]\nshape: done\n'
           'save to db.loose\n    a "1"\n    b "2"\nsave: done\n')
e = of(it, 'save', 'loose')
check("a table with no uniqueness reports 'unknown' identity", e.get('record_identity') == 'unknown', e)
# With no declared identity there is nothing to render, so whatever surrogate the driver gave
# (SQLite's rowid here) is left as-is -- and the 'unknown' shape is what stops a reader taking it
# for a real key. Nothing is invented; the record says exactly how much it knows.
check("...and does not fabricate a composite for it",
      'composite' not in str(e.get('record_identity')), e)

# -- UPDATE / REMOVE on a composite table: shape recorded (values deliberately not) ----------
it = run_sqlite(
    ['CREATE TABLE flags (session_id TEXT, flag_name TEXT, note TEXT, '
     'UNIQUE (session_id, flag_name))'],
    HEAD + 'shape Flag\n    session_id as text\n    flag_name as text\n'
           '    note as text [pii]\nshape: done\n'
           'save to db.flags\n    session_id "s9"\n    flag_name "door_open"\n    note "n"\n'
           'save: done\n'
           'update db.flags\n    match session_id to "s9"\n    flag_name "door_shut"\n'
           'update: done\n'
           'remove from db.flags\n    match session_id to "s9"\nremove: done\n')
u, r = of(it, 'update', 'flags'), of(it, 'remove', 'flags')
check("update on a composite table records the identity shape",
      u.get('record_identity') == 'composite(session_id,flag_name)', u)
check("remove on a composite table records the identity shape",
      r.get('record_identity') == 'composite(session_id,flag_name)', r)
# SUPERSEDED within this same unit (Finding C, ruled 2026-08-21). This case asserted that update
# recorded match column NAMES and never their values -- the behaviour that shipped before the
# ruling. Ron ruled the opposite and for a stated reason: a mutation must be as identifiable as an
# insert, so update/remove now redact-and-record like save. What survives unchanged is the part
# that always mattered -- match_fields still names the columns, and no TAGGED value is ever stored.
check("update names its match columns AND records the identity, tagged parts protected",
      u.get('match_fields') == ['session_id']
      and u.get('record_id') == 'session_id=s9,flag_name=<absent>', u)

# -- FINDING C (ruled 2026-08-21): a MUTATION is as identifiable as an insert ----------------
# update/remove used to record match column NAMES and a count -- never which row. They now
# redact-and-record exactly as save does, because "which row was updated/deleted" is a core audit
# question. The match CONDITIONS are the identity values; an identity column not used to narrow
# the operation renders `<absent>`, which is honest -- `count` says how many rows it reached.
MIXED_DDL = ['CREATE TABLE mixed (clinic_id TEXT, ssn TEXT, note TEXT, UNIQUE (clinic_id, ssn))']
MIXED_SHAPE = ('shape Mixed\n    clinic_id as text\n    ssn as text [phi]\n'
               '    note as text\nshape: done\n')
MIXED_SAVE = ('save to db.mixed\n    clinic_id "c1"\n    ssn "' + SSN + '"\n'
              '    note "n"\nsave: done\n')

it = run_sqlite(MIXED_DDL, HEAD + MIXED_SHAPE + MIXED_SAVE
                + 'update db.mixed\n    match clinic_id to "c1"\n    note "changed"\nupdate: done\n'
                + 'remove from db.mixed\n    match clinic_id to "c1"\nremove: done\n')
u, r = of(it, 'update', 'mixed'), of(it, 'remove', 'mixed')
check("update RECORDS the row it changed, not just the column names",
      u.get('record_id') == 'clinic_id=c1,ssn=<absent>', u)
check("remove RECORDS the row it deleted", r.get('record_id') == 'clinic_id=c1,ssn=<absent>', r)
check("update leaks no tagged value while doing it",
      SSN not in json.dumps(u, default=str), u)

# A remove whose match COVERS a tagged identity column must redact it, not render it.
it = run_sqlite(MIXED_DDL, HEAD + MIXED_SHAPE + MIXED_SAVE
                + 'remove from db.mixed\n    match ssn to "' + SSN + '"\nremove: done\n')
r = of(it, 'remove', 'mixed')
check("a mutation matched ON a tagged column redacts it in the record",
      'ssn=<redacted>' in str(r.get('record_id')), r)
check("...and that tagged value never reaches the audit log",
      SSN not in json.dumps(entries(it), default=str), r)


# A remove with TWO match conditions takes `remove_multi`, a DIFFERENT code path from the
# single-condition remove above. Found by mutation: reverting the multi path to shape-only left
# every case here green, because nothing exercised it. Both paths are now covered.
it = run_sqlite(MIXED_DDL, HEAD + MIXED_SHAPE + MIXED_SAVE
                + 'remove from db.mixed\n    match clinic_id to "c1", ssn to "' + SSN + '"\n'
                + 'remove: done\n')
r = of(it, 'remove', 'mixed')
check("multi-condition remove (remove_multi path) also records the identity",
      r.get('record_id') == 'clinic_id=c1,ssn=<redacted>', r)
check("multi-condition remove leaks no tagged value",
      SSN not in json.dumps(entries(it), default=str), r)



# -- THE ADDITION: an ALL-sensitive identity must announce the gap, not hide it ---------------
# Redacting every part leaves a record that identifies NO row while reading exactly like an
# ordinary partial redaction. The record says so, and names the item that will close it.
MARKER = 'row-identification pending: T1-AUDIT-SURROGATE-IDENTITY'
it = run_sqlite(
    ['CREATE TABLE allsens (mrn TEXT, ssn TEXT, note TEXT, UNIQUE (mrn, ssn))'],
    HEAD + 'shape AllSens\n    mrn as text [phi]\n    ssn as text [phi]\n'
           '    note as text\nshape: done\n'
           'save to db.allsens\n    mrn "MRN-9"\n    ssn "' + SSN + '"\n'
           '    note "n"\nsave: done\n'
           'update db.allsens\n    match mrn to "MRN-9"\n    note "changed"\nupdate: done\n')
e, u = of(it, 'save', 'allsens'), of(it, 'update', 'allsens')
check("all-sensitive identity carries the SPECIFIC limitation marker",
      MARKER in str(e.get('record_identity')), e)
check("...and still names the identity shape alongside it",
      'composite(mrn,ssn)' in str(e.get('record_identity')), e)
check("a MUTATION on an all-sensitive table carries the marker too",
      MARKER in str(u.get('record_identity')), u)
check("no surrogate hash was invented in its place",
      'sha' not in str(e.get('record_id')).lower() and len(str(e.get('record_id'))) < 120, e)

# A merely PARTIALLY-redacted identity must NOT carry the marker -- that is the distinction.
it = run_sqlite(MIXED_DDL, HEAD + MIXED_SHAPE + MIXED_SAVE)
check("a partially-redacted identity does NOT claim the limitation (it identifies a row)",
      MARKER not in str(of(it, 'save', 'mixed').get('record_identity')),
      of(it, 'save', 'mixed'))



# -- The identity source itself: the database, not a copied map ------------------------------
db = DbRuntime(':memory:')
db.conn.execute('CREATE TABLE flags (session_id TEXT, flag_name TEXT, UNIQUE (session_id, flag_name))')
db.conn.execute('CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT)')
db.conn.execute('CREATE TABLE loose (a TEXT)')
db.conn.commit()
check("table_identity reads the composite from the schema",
      db.table_identity('flags') == ('session_id', 'flag_name'), db.table_identity('flags'))
check("table_identity reads a primary key", db.table_identity('people') == ('id',),
      db.table_identity('people'))
check("table_identity reports () when the table guarantees nothing",
      db.table_identity('loose') == (), db.table_identity('loose'))


# -- LIVE Postgres ---------------------------------------------------------------------------
_URL = os.environ.get('MOHIO_TEST_PG_URL')
if not _URL:
    print("  [SKIP] live: set MOHIO_TEST_PG_URL to run the real-Postgres audit cases")
else:
    import psycopg2
    conn = psycopg2.connect(_URL); conn.autocommit = True
    c = conn.cursor()
    c.execute('DROP TABLE IF EXISTS t_flags, t_people')
    c.execute('CREATE TABLE t_flags ("session_id" TEXT, "flag_name" TEXT, "note" TEXT, '
              'UNIQUE ("session_id","flag_name"))')
    c.execute('CREATE TABLE t_people ("id" TEXT PRIMARY KEY, "name" TEXT, "secret" TEXT)')
    prev = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = _URL
    try:
        SRC = ('connect db as postgres from env.DATABASE_URL\n'
               'shape Flag\n    session_id as text\n    flag_name as text\n'
               '    note as text [pii]\nshape: done\n'
               'shape Person\n    id as text\n    name as text\n    secret as text [pii]\n'
               'shape: done\n'
               'save to db.t_flags\n    session_id "s9"\n    flag_name "door_open"\n'
               '    note "n"\nsave: done\n'
               'save to db.t_people\n    id "p1"\n    name "Bo"\n    secret "x"\nsave: done\n')
        it = MohioInterpreter()
        prog = ast_transform(P.parse(SRC), SRC)
        it.run_declarations(prog); it.run(prog)
        e = of(it, 'save', 't_flags')
        check("live: composite save records the real identity on Postgres",
              e.get('record_id') == 'session_id=s9,flag_name=door_open', e)
        check("live: composite shape recorded on Postgres",
              e.get('record_identity') == 'composite(session_id,flag_name)', e)
        e = of(it, 'save', 't_people')
        check("live: keyed save still records its bare id on Postgres",
              e.get('record_id') == 'p1' and e.get('record_identity') == 'id', e)
    except Exception as ex:
        check("live: Postgres audit identity", False, f"{type(ex).__name__}: {ex}")
    finally:
        if prev is not None: os.environ['DATABASE_URL'] = prev
        c.close(); conn.close()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
