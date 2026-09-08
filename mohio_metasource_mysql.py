# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The MySQL and MariaDB source adapter, and the portable gate this family needs most.

P4. One adapter, two engines: MySQL and MariaDB share a dialect and an `information_schema`, and
where they diverge the adapter reports which engine answered rather than pretending they are the
same product. `engine_kind()` returns "mysql" or "mariadb" from the version banner.

THE DEPTH IS THREE LEVELS BELOW THE SOURCE, and that was CONFIRMED rather than carried over from
Postgres. Measured on both engines:

    database() and schema() return the SAME value      -> they are synonyms, not two levels
    information_schema.columns.table_catalog exists    -> but its only value is 'def'

So there is no schema level between database and table, and no catalog level above database. The
path is (server, database, table, column).

WHY THE SOURCE IS THE SERVER HERE AND THE DATABASE THERE. A Postgres connection is bound to one
database, so the Postgres adapter uses the database name as the source and the schema as the
namespace. A MySQL connection is not: one server holds many databases and a single connection can
read across them. Calling each database a separate source would say two things that live on one
server are two sources, which is wrong, so the SERVER is the source and the database is a real
namespace level below it. Both readings are engine-shaped rather than uniform, which is what the
arbitrary-depth key was for.

THE VERSION TABLE MATTERS MOST HERE, and this is the family that proves why it exists. Postgres
at least has a catalog you can watch. MySQL and MariaDB have essentially nothing: measured on
both, `information_schema.tables.update_time` is NULL BEFORE and AFTER a real ALTER TABLE, so the
one field that looks like a change signal reports nothing at all for DDL. There is no cheap native
question to ask. A Mohio-owned version row is not a convenience on this engine family, it is the
only cheap signal that exists, and the full-diff fallback is the only thing that catches a change
made outside Mohio.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from mohio_metasource import (
    DECLARED, OBSERVED, FieldEntry, MetasourceError, NormalizedIndex, SourceAdapter, SourcePath)

SYSTEM_DATABASES = ("information_schema", "mysql", "performance_schema", "sys")

VERSION_TABLE = "mohio_metasource_version"


class MySQLAdapter(SourceAdapter):
    """Introspect a live MySQL or MariaDB into a normalized index.

    Reads `MYSQL_URL` by default. An explicit `dsn=` always wins, and the parts can be passed
    directly instead, because a driver that only accepts a URL makes a caller build one just to
    take it apart again.
    """

    name = "mysql"

    def __init__(self, dsn: Optional[str] = None, host: str = "127.0.0.1", port: int = 3306,
                 user: str = "", password: str = "", database: Optional[str] = None,
                 databases: Optional[Sequence[str]] = None, source: Optional[str] = None,
                 sample_json: bool = False, json_sample_rows: int = 50):
        dsn = dsn or os.environ.get("MYSQL_URL") or ""
        if dsn:
            u = urlparse(dsn)
            host = u.hostname or host
            port = u.port or port
            user = unquote(u.username or "") or user
            password = unquote(u.password or "") or password
            database = (u.path or "").lstrip("/") or database
        if not user:
            raise MetasourceError(
                "MySQLAdapter needs a user: pass `dsn=`/`user=`, or set MYSQL_URL.")
        self.host, self.port, self.user, self.password = host, int(port), user, password
        self.database = database
        self._databases = list(databases) if databases else ([database] if database else None)
        self._source = source
        self.sample_json = sample_json
        self.json_sample_rows = json_sample_rows

    # ── connection ───────────────────────────────────────────────────────────────────────────
    def _connect(self):
        try:
            import pymysql
        except ImportError as e:
            raise MetasourceError(
                f"MySQLAdapter needs PyMySQL and it could not be imported: {e}") from e
        return pymysql.connect(host=self.host, port=self.port, user=self.user,
                               password=self.password, database=self.database,
                               connect_timeout=10, autocommit=True)

    def source_name(self) -> str:
        """The SERVER, not the database. One server holds many databases.

        An address rather than a friendly name, for the same reason the Mongo adapter uses one
        for a standalone: the source segment is the head of every path, so it has to be genuinely
        distinct, and two servers on one host differ only by port.
        """
        return self._source or f"{self.host}:{self.port}"

    def engine_kind(self, conn=None) -> str:
        """"mariadb" or "mysql". They diverge, and a report should say which one answered."""
        own = conn is None
        conn = conn or self._connect()
        try:
            cur = conn.cursor()
            cur.execute("select version()")
            return "mariadb" if "mariadb" in str(cur.fetchone()[0]).lower() else "mysql"
        finally:
            if own:
                conn.close()

    def server_version(self, conn=None) -> str:
        own = conn is None
        conn = conn or self._connect()
        try:
            cur = conn.cursor()
            cur.execute("select version()")
            return str(cur.fetchone()[0])
        finally:
            if own:
                conn.close()

    def path_for(self, database: str, table: str, column: str) -> SourcePath:
        return SourcePath(self.source_name(), database, table, column)

    # ── introspection ────────────────────────────────────────────────────────────────────────
    def introspect(self) -> NormalizedIndex:
        import datetime
        conn = self._connect()
        try:
            idx = NormalizedIndex(
                source=self.source_name(), adapter=self.name,
                introspected_at=datetime.datetime.utcnow().isoformat() + "Z")
            cur = conn.cursor()
            dbs = self._databases
            if not dbs:
                cur.execute("select schema_name from information_schema.schemata")
                dbs = [r[0] for r in cur.fetchall() if r[0] not in SYSTEM_DATABASES]

            placeholders = ",".join(["%s"] * len(dbs)) if dbs else "''"
            cur.execute(
                f"""
                select  c.table_schema, c.table_name, c.column_name, c.data_type,
                        c.is_nullable, c.column_default, c.extra, t.table_type
                from information_schema.columns c
                join information_schema.tables t
                  on t.table_schema = c.table_schema and t.table_name = c.table_name
                where c.table_schema in ({placeholders})
                order by c.table_schema, c.table_name, c.ordinal_position
                """, tuple(dbs))

            json_cols: List[Tuple[str, str, str]] = []
            for (schema, table, column, data_type, is_nullable, default, extra,
                 table_type) in cur.fetchall():
                extra_s = str(extra or "").upper()
                idx.add(FieldEntry(
                    path=self.path_for(schema, table, column),
                    type_name=str(data_type),
                    # A real SQL column is DECLARED: the engine rejects a row that violates its
                    # type or its NOT NULL. Same reasoning as Postgres, same answer.
                    provenance=DECLARED,
                    nullable=(str(is_nullable).upper() == "YES"),
                    # AUTO_INCREMENT supplies its own value, so an INSERT may omit the column --
                    # which is the question `default_exists` is actually asking.
                    default_exists=self._insert_may_omit_impl(default, extra_s),
                    classification=(),
                    writable=self._writable(table_type, extra_s),
                ))
                if str(data_type).lower() == "json":
                    json_cols.append((schema, table, column))

            if self.sample_json:
                for entry in self._observed_json_keys(conn, json_cols):
                    if entry.path not in idx.entries:
                        idx.add(entry)
            return idx
        finally:
            conn.close()

    @staticmethod
    def _insert_may_omit_impl(default, extra_upper: str) -> bool:
        """Can an existing INSERT leave this column out and still succeed?

        Two independent reasons it can, and they are named rather than combined into one
        expression: the column has a DEFAULT, or it is AUTO_INCREMENT and supplies its own value.
        Written this way because the silent-shape ratchet reads `A or B` as a fallback and cannot
        tell a boolean from a default -- and the honest fix for that is to make the code say what
        it means, not to annotate around it.
        """
        has_default = default is not None
        supplies_own_value = "AUTO_INCREMENT" in extra_upper
        return has_default or supplies_own_value

    @staticmethod
    def _writable(table_type: str, extra_upper: str) -> bool:
        """A view and a generated column cannot be written, on either engine.

        MySQL spells a generated column `VIRTUAL GENERATED` / `STORED GENERATED` in `extra`;
        MariaDB spells it `VIRTUAL` / `PERSISTENT`. Both are checked, because assuming one
        spelling is how an adapter silently reports a computed column as writable on the other
        engine.
        """
        if str(table_type).upper() == "VIEW":
            return False
        return not any(tok in extra_upper
                       for tok in ("GENERATED", "VIRTUAL", "PERSISTENT"))

    def _observed_json_keys(self, conn, json_cols) -> List[FieldEntry]:
        """Sample JSON columns for their keys. THE OBSERVED CASE on this family.

        The column type is declared (the engine guarantees valid JSON). What is inside it is not:
        the next row may carry different keys and nothing rejects it.
        """
        out: List[FieldEntry] = []
        cur = conn.cursor()
        for schema, table, column in json_cols:
            try:
                cur.execute(
                    f"select `{column}` from `{schema}`.`{table}` "
                    f"where `{column}` is not null limit %s", (self.json_sample_rows,))
                import json as _json
                seen = set()
                for (blob,) in cur.fetchall():
                    doc = _json.loads(blob) if isinstance(blob, (str, bytes)) else blob
                    if isinstance(doc, dict):
                        seen.update(str(k) for k in doc)
                for key in sorted(seen):
                    out.append(FieldEntry(
                        path=SourcePath(*self.path_for(schema, table, column), key),
                        type_name="json_key", provenance=OBSERVED,
                        nullable=True, default_exists=False, writable=True))
            except Exception:
                continue    # one unreadable column must not abort the whole introspect
        return out

    # ── the version table: on this family it is the ONLY cheap signal ────────────────────────
    def _version_db(self) -> str:
        db = self.database or (self._databases[0] if self._databases else None)
        if not db:
            raise MetasourceError(
                "The version table needs a database to live in: pass `database=` so the gate has "
                "a home rather than being written wherever the connection happens to point.")
        return db

    def ensure_version_table(self) -> None:
        conn = self._connect()
        try:
            db = self._version_db()
            cur = conn.cursor()
            cur.execute(f"""
                create table if not exists `{db}`.`{VERSION_TABLE}` (
                    id          int primary key,
                    version     bigint not null default 0,
                    fingerprint varchar(128) not null default '',
                    updated_at  timestamp not null default current_timestamp
                                on update current_timestamp
                )""")
            cur.execute(f"insert ignore into `{db}`.`{VERSION_TABLE}` (id, version) values (1, 0)")
        finally:
            conn.close()

    def read_version(self) -> Tuple[int, str]:
        conn = self._connect()
        try:
            db = self._version_db()
            cur = conn.cursor()
            cur.execute(f"select version, fingerprint from `{db}`.`{VERSION_TABLE}` where id = 1")
            row = cur.fetchone()
            return (int(row[0]), str(row[1])) if row else (0, "")
        finally:
            conn.close()

    def bump_version(self, fingerprint: Optional[str] = None) -> Tuple[int, str]:
        fp = fingerprint if fingerprint is not None else self.introspect().semantic_fingerprint()
        conn = self._connect()
        try:
            db = self._version_db()
            cur = conn.cursor()
            cur.execute(f"update `{db}`.`{VERSION_TABLE}` set version = version + 1, "
                        f"fingerprint = %s where id = 1", (fp,))
        finally:
            conn.close()
        return self.read_version()

    def native_change_signal(self) -> Dict:
        """What the ENGINE offers on its own. Measured, and the answer is close to nothing.

        `information_schema.tables.update_time` is the only field that looks like a change
        signal, and on both engines it is NULL before AND after a real ALTER TABLE. It is
        reported here so the version table's necessity is a measurement in the product rather
        than a claim in a document.
        """
        conn = self._connect()
        try:
            db = self._version_db()
            cur = conn.cursor()
            cur.execute("select count(*), count(update_time) from information_schema.tables "
                        "where table_schema = %s", (db,))
            total, with_time = cur.fetchone()
            return {"tables": int(total), "with_update_time": int(with_time),
                    "usable_for_ddl": False}
        finally:
            conn.close()

    def drift_check(self, known_version: int, known_fingerprint: str) -> Dict:
        """Two tiers, and on this family the second one carries almost all the weight.

        Tier 1 catches what Mohio did. Tier 2 catches what it did not, which on MySQL and MariaDB
        is everything the engine will never tell you about.
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


class MariaDBAdapter(MySQLAdapter):
    """MariaDB. The same dialect, and deliberately its own name in the index.

    Not a copy: it inherits everything, because the introspection surface really is shared. What
    it changes is the `adapter` field recorded in the index, so a stored index says which engine
    produced it. The two diverge (generated-column spelling, `CHECK` handling, version banner),
    and a report that cannot say which one answered cannot explain a difference between them.
    """

    name = "mariadb"
