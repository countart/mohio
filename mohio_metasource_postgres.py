# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The Postgres source adapter, and the Mohio-owned version table that makes checking it cheap.

P2. Implements the P1 `SourceAdapter` interface against a real Postgres (and Supabase, which is
Postgres with a connection string and a different default set of schemas). Nothing here reaches
into Mohio's own runtime; an adapter's whole job is to say what is THERE and how sure it is.

THE SCHEMA LEVEL IS REAL HERE, which is why P1's key had to be arbitrary-depth. Postgres names a
column as `database.schema.table.column`, four levels, and pretending it is three would either
drop the schema (making `public.users.email` and `billing.users.email` the same field, which is
the Q533 collision arriving from the source side) or force a synthetic segment onto every source
that has no schema at all.

    public schema      -> ("mydb", "users", "email")               3 segments
    any other schema   -> ("mydb", "billing", "users", "email")    4 segments

The public schema is OMITTED, not padded. `public` is Postgres's "no schema was specified", so
carrying it would put a segment in the path that the developer never wrote and never sees in
their own SQL. A non-default schema is a real namespace the developer chose, so it stays. Both
readings are unambiguous because a Postgres source cannot have a table named the same as one of
its schemas at the same level.

PROVENANCE IS DECLARED FOR A REAL SQL COLUMN, and that is not a shortcut. The database enforces
the type and the nullability: an INSERT that violates either is rejected by the engine, not by a
convention. That is exactly what DECLARED means. Postgres's OBSERVED cases are the ones where the
engine is not enforcing the shape -- keys sampled out of a `json`/`jsonb` column, where the column
type guarantees "this is valid JSON" and guarantees nothing whatever about what is inside it.

THE VERSION TABLE IS A CHEAP GATE, NOT THE TRUTH. One row that Mohio owns and bumps when it makes
a managed change. Reading it is a single indexed lookup, so a request path can afford to check it
constantly. It CANNOT see a change made outside Mohio (a DBA running ALTER TABLE by hand, a
migration from another service), which is exactly why the full diff stays and runs periodically.
A gate that could be trusted alone would have to be enforced by the database, and nothing stops a
superuser. So: the version table answers "has anything Mohio did changed this?" cheaply and
often, and the full introspect answers "is the source still what we last recorded?" slowly and
rarely. Neither replaces the other.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from mohio_metasource import (
    DECLARED, OBSERVED, FieldEntry, MetasourceError, NormalizedIndex, SourceAdapter, SourcePath)

# The schemas Postgres and its extensions own. A source index describes the APPLICATION's data;
# sweeping in the catalog would bury it under thousands of entries nobody declared.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

VERSION_TABLE = "mohio_metasource_version"


class PostgresAdapter(SourceAdapter):
    """Introspect a live Postgres into a normalized index."""

    name = "postgres"

    def __init__(self, dsn: Optional[str] = None, source: Optional[str] = None,
                 schemas: Optional[Sequence[str]] = None, sample_json: bool = False,
                 json_sample_rows: int = 50):
        self.dsn = dsn or os.environ.get("DATABASE_URL") or ""
        if not self.dsn:
            raise MetasourceError(
                "PostgresAdapter needs a connection string: pass `dsn=` or set DATABASE_URL.")
        self._source = source
        self._schemas = list(schemas) if schemas else None
        # Sampling a json column's KEYS is the one OBSERVED case Postgres has. Off by default: it
        # reads rows, and an adapter that quietly reads application data on every introspect is
        # not something to turn on for someone.
        self.sample_json = sample_json
        self.json_sample_rows = json_sample_rows

    # ── connection ───────────────────────────────────────────────────────────────────────────
    def _connect(self):
        try:
            import psycopg2
        except ImportError as e:
            raise MetasourceError(
                f"PostgresAdapter needs psycopg2 and it could not be imported: {e}") from e
        return psycopg2.connect(self.dsn, connect_timeout=10)

    def database_name(self, conn=None) -> str:
        if self._source:
            return self._source
        own = conn is None
        conn = conn or self._connect()
        try:
            cur = conn.cursor()
            cur.execute("select current_database()")
            return str(cur.fetchone()[0])
        finally:
            if own:
                conn.close()

    # ── the path rule ────────────────────────────────────────────────────────────────────────
    def path_for(self, database: str, schema: str, table: str, column: str) -> SourcePath:
        """`public` is omitted; any other schema is a real namespace level and is kept."""
        if schema == "public":
            return SourcePath(database, table, column)
        return SourcePath(database, schema, table, column)

    # ── introspection ────────────────────────────────────────────────────────────────────────
    def introspect(self) -> NormalizedIndex:
        import datetime
        conn = self._connect()
        try:
            db = self.database_name(conn)
            idx = NormalizedIndex(
                source=db, adapter=self.name,
                introspected_at=datetime.datetime.utcnow().isoformat() + "Z")
            cur = conn.cursor()
            # One query for structure. `is_generated`/`identity_generation` decide writability
            # alongside the relation kind, and `c.relkind` distinguishes a table from a view.
            cur.execute(
                """
                select  col.table_schema,
                        col.table_name,
                        col.column_name,
                        col.data_type,
                        col.is_nullable,
                        col.column_default,
                        col.is_generated,
                        col.identity_generation,
                        col.is_updatable,
                        cls.relkind
                from information_schema.columns col
                join pg_catalog.pg_class cls
                  on cls.relname = col.table_name
                join pg_catalog.pg_namespace ns
                  on ns.oid = cls.relnamespace and ns.nspname = col.table_schema
                where col.table_schema not in %s
                order by col.table_schema, col.table_name, col.ordinal_position
                """,
                (tuple(self._schemas) if self._schemas else SYSTEM_SCHEMAS,)
                if self._schemas is None else (tuple(SYSTEM_SCHEMAS),))
            rows = cur.fetchall()
            if self._schemas is not None:
                rows = [r for r in rows if r[0] in self._schemas]

            for (schema, table, column, data_type, is_nullable, default,
                 is_generated, identity, is_updatable, relkind) in rows:
                idx.add(FieldEntry(
                    path=self.path_for(db, schema, table, column),
                    type_name=str(data_type),
                    # A REAL SQL COLUMN IS DECLARED. The engine rejects a row that violates its
                    # type or its NOT NULL, which is the definition: something enforces it.
                    provenance=DECLARED,
                    nullable=(str(is_nullable).upper() == "YES"),
                    # An identity column supplies its own value, so an INSERT that omits it
                    # succeeds -- which is what `default_exists` is asked to predict.
                    default_exists=(default is not None or bool(identity)),
                    classification=(),   # Postgres has none of its own; Mohio's tags are separate
                    writable=self._writable(relkind, is_generated, is_updatable),
                ))

            if self.sample_json:
                for entry in self._observed_json_keys(conn, db, rows):
                    idx.add(entry)
            return idx
        finally:
            conn.close()

    @staticmethod
    def _writable(relkind: str, is_generated: str, is_updatable: str) -> bool:
        """A column you cannot write is not a column you can migrate data into.

        Three separate ways Postgres says no, and they are not interchangeable:
          * relkind 'v'/'m' -- a view or materialized view
          * is_generated    -- a GENERATED ALWAYS column: the engine computes it
          * is_updatable    -- information_schema's own verdict, which catches the cases the
                               other two miss (a view with no INSTEAD OF trigger, for one)
        """
        if str(relkind) in ("v", "m"):
            return False
        if str(is_generated).upper() == "ALWAYS":
            return False
        return str(is_updatable).upper() != "NO"

    def _observed_json_keys(self, conn, db: str, rows) -> List[FieldEntry]:
        """Sample json/jsonb columns for their keys. THE OBSERVED CASE, and it is honest about it.

        The column's TYPE is declared (the engine guarantees valid JSON). What is inside it is
        not: the next row may have different keys, and nothing rejects it. So every key found
        this way is OBSERVED, at a path one level deeper than the column.
        """
        out: List[FieldEntry] = []
        cur = conn.cursor()
        for (schema, table, column, data_type, *_rest) in rows:
            if str(data_type) not in ("json", "jsonb"):
                continue
            try:
                cur.execute(
                    f'select distinct k from (select jsonb_object_keys({column}::jsonb) as k '
                    f'from "{schema}"."{table}" '
                    f'where {column} is not null limit %s) s',
                    (self.json_sample_rows,))
                for (key,) in cur.fetchall():
                    out.append(FieldEntry(
                        path=SourcePath(*self.path_for(db, schema, table, column), str(key)),
                        type_name="json_key",
                        provenance=OBSERVED,
                        nullable=True,
                        default_exists=False,
                        writable=True,
                    ))
            except Exception:
                conn.rollback()   # a malformed row must not abort the whole introspect
        return out

    # ── the version table: the cheap gate ────────────────────────────────────────────────────
    def ensure_version_table(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                create table if not exists {VERSION_TABLE} (
                    id            int primary key default 1,
                    version       bigint not null default 0,
                    fingerprint   text   not null default '',
                    updated_at    timestamptz not null default now(),
                    constraint {VERSION_TABLE}_single_row check (id = 1)
                )""")
            cur.execute(f"insert into {VERSION_TABLE} (id) values (1) on conflict (id) do nothing")
            conn.commit()
        finally:
            conn.close()

    def read_version(self) -> Tuple[int, str]:
        """One indexed lookup. Cheap enough for a request path to check constantly."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"select version, fingerprint from {VERSION_TABLE} where id = 1")
            row = cur.fetchone()
            return (int(row[0]), str(row[1])) if row else (0, "")
        finally:
            conn.close()

    def bump_version(self, fingerprint: Optional[str] = None) -> Tuple[int, str]:
        """Record that Mohio changed something. Called by the managed-change path, not by a scan."""
        fp = fingerprint if fingerprint is not None else self.introspect().semantic_fingerprint()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"update {VERSION_TABLE} set version = version + 1, fingerprint = %s, "
                f"updated_at = now() where id = 1 returning version, fingerprint", (fp,))
            row = cur.fetchone()
            conn.commit()
            return (int(row[0]), str(row[1]))
        finally:
            conn.close()

    def drift_check(self, known_version: int, known_fingerprint: str) -> Dict:
        """The two-tier check, and the second tier is why the first is allowed to be cheap.

        Tier 1, the gate: has the version row moved? One lookup. Catches everything Mohio did.
        Tier 2, the fallback: does the live fingerprint still match what was recorded? A full
        introspect. Catches what Mohio did NOT do -- a DBA at a psql prompt, another service's
        migration -- which tier 1 structurally cannot see, because nothing made that change go
        through Mohio.

        Returning both verdicts separately is deliberate. Collapsing them into one boolean would
        hide the interesting case: version unchanged but fingerprint moved IS the out-of-band
        change, and it is the one worth waking someone for.
        """
        version, recorded_fp = self.read_version()
        live_fp = self.introspect().semantic_fingerprint()
        return {
            "version": version,
            "version_moved": version != known_version,
            "recorded_fingerprint": recorded_fp,
            "live_fingerprint": live_fp,
            "fingerprint_moved": live_fp != known_fingerprint,
            "out_of_band": (version == known_version and live_fp != known_fingerprint),
        }
