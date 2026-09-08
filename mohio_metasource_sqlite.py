# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The SQLite source adapter. A file, no server, and the sharpest provenance case in the family.

P5. This is the no-infrastructure case: the smallest footprint Mohio has, and the one that keeps
the no-external-database path honest. It is also the engine that validates P1's per-field
provenance decision most sharply, for a reason none of the server engines surface.

A NON-STRICT SQLITE COLUMN'S TYPE IS ADVISORY. Measured, on a plain non-rowid column:

    non-STRICT `qty integer`  given 'not-a-number'  ->  stored, as TEXT
    STRICT     `qty integer`  given 'not-a-number'  ->  rejected
    NOT NULL                                        ->  enforced in both cases

So within ONE relational engine, whether the recorded shape can be trusted varies TABLE BY TABLE,
decided by whether the table was created STRICT. A per-source flag cannot express that at all,
and a per-TABLE flag would still be the wrong shape for the mixed-validator Mongo case. Per-field
is the only reading that covers both, which is why P1 chose it.

  STRICT table      -> the engine enforces the type      -> DECLARED
  non-STRICT table  -> the engine does not               -> OBSERVED

NULLABILITY IS ENFORCED EITHER WAY, and that is worth stating because it looks like a
contradiction and is not. Provenance answers one question: can the recorded SHAPE be trusted? For
a non-STRICT column the answer is no, because the type can be anything the last writer put there.
That a different attribute of the same column happens to be enforced does not make the shape
guaranteed, and rounding up to DECLARED would put a compliance claim on a column that will
cheerfully hold a string where the schema says integer.

THE GATE IS NATIVE HERE, and this is the third distinct answer across the engine family:

    MySQL / MariaDB   no usable native signal          -> a Mohio-owned version table is the
                                                          only cheap question there is
    Postgres          a catalog worth watching         -> Mohio-owned table, plus the full diff
    SQLite            `pragma schema_version`          -> a real counter the ENGINE bumps on
                                                          every schema change, measured moving
                                                          from 2 to 3 across one ALTER

So SQLite needs no Mohio-owned table for the cheap tier. Using the engine's own counter is better
than shadowing it: `schema_version` moves for a change made by ANY writer, including one that
never went through Mohio, which is precisely the case the server engines need a full diff to
catch. The full diff stays regardless, because the counter says THAT something changed and never
WHAT.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from mohio_metasource import (
    DECLARED, OBSERVED, FieldEntry, MetasourceError, NormalizedIndex, SourceAdapter, SourcePath)

# SQLite's own bookkeeping tables. A source index describes the application's data.
INTERNAL_PREFIXES = ("sqlite_",)


class SQLiteAdapter(SourceAdapter):
    """Introspect a SQLite file into a normalized index. No server, no credentials."""

    name = "sqlite"

    def __init__(self, path: Optional[str] = None, source: Optional[str] = None,
                 attach: Optional[Dict[str, str]] = None):
        self.path = path or os.environ.get("SQLITE_PATH") or ""
        if not self.path:
            raise MetasourceError(
                "SQLiteAdapter needs a file: pass `path=` or set SQLITE_PATH.")
        if self.path != ":memory:" and not os.path.exists(self.path):
            raise MetasourceError(
                f"There is no SQLite file at {self.path!r}. An adapter that quietly created one "
                f"would report an empty source as though the real one were empty.")
        self._source = source
        # ATTACHed databases are a real extra namespace level, so they are opened deliberately
        # rather than discovered: a caller says which ones belong to this source.
        self.attach = dict(attach or {})

    def _connect(self):
        conn = sqlite3.connect(self.path)
        for alias, file in self.attach.items():
            conn.execute(f"attach database ? as {alias}", (file,))
        return conn

    def source_name(self) -> str:
        """The FILE, absolutely resolved.

        Two files both called `app.db` in different folders are two different sources, and the
        source segment is the head of every path, so a bare basename would collide exactly the
        way a shared placeholder would.
        """
        if self._source:
            return self._source
        return self.path if self.path == ":memory:" else os.path.abspath(self.path)

    def path_for(self, database: str, table: str, column: str) -> SourcePath:
        """`main` is omitted; an ATTACHed name is a real level and is kept.

        Same rule as Postgres's `public`: the default name is what the engine says when no
        namespace was chosen, so carrying it would put a segment in the path the developer never
        wrote. An attached database is a namespace they did choose.
        """
        if database == "main":
            return SourcePath(self.source_name(), table, column)
        return SourcePath(self.source_name(), database, table, column)

    # ── introspection ────────────────────────────────────────────────────────────────────────
    def introspect(self) -> NormalizedIndex:
        import datetime
        conn = self._connect()
        try:
            idx = NormalizedIndex(
                source=self.source_name(), adapter=self.name,
                introspected_at=datetime.datetime.utcnow().isoformat() + "Z")
            for (_seq, dbname, _file) in conn.execute("pragma database_list").fetchall():
                for name, kind, ddl in conn.execute(
                        f"select name, type, sql from {dbname}.sqlite_master "
                        f"where type in ('table','view')").fetchall():
                    if str(name).startswith(INTERNAL_PREFIXES):
                        continue
                    is_view = (kind == "view")
                    # STRICT is a property of the TABLE, and the only place it is recorded is
                    # the DDL text sqlite_master keeps. There is no pragma for it.
                    strict = self._is_strict(ddl)
                    for col in conn.execute(
                            f"pragma {dbname}.table_info('{name}')").fetchall():
                        _cid, colname, decltype, notnull, dflt, _pk = col
                        # A SQLITE COLUMN MAY HAVE NO DECLARED TYPE AT ALL. `create table t (x)`
                        # is legal, and `pragma table_info` returns an empty string for it.
                        # Filling that in with "blob" would invent a declaration the developer
                        # never wrote, in the one field the diff compares for breaking type
                        # changes -- so a later `create table t (x blob)` would look like no
                        # change when the schema genuinely gained a type. SQLite's own affinity
                        # rules do say a typeless column behaves as BLOB, which is exactly what
                        # makes the guess tempting and wrong: affinity is behaviour, and this
                        # field records what the SCHEMA SAYS. It says nothing, so this says so.
                        _dt = str(decltype).strip().lower() if decltype else ""
                        declared_type = _dt if _dt else "(untyped)"
                        idx.add(FieldEntry(
                            path=self.path_for(dbname, str(name), str(colname)),
                            # The DECLARED AFFINITY is recorded either way, because it is what
                            # the schema says. Provenance is what tells you whether to believe
                            # it, which is the entire division of labour between the two fields.
                            type_name=declared_type,
                            provenance=DECLARED if strict else OBSERVED,
                            nullable=not bool(notnull),
                            default_exists=(dflt is not None),
                            classification=(),
                            writable=not is_view,
                        ))
            return idx
        finally:
            conn.close()

    @staticmethod
    def _is_strict(ddl: Optional[str]) -> bool:
        """STRICT sits after the closing paren of a CREATE TABLE, and nowhere else.

        Read from the tail rather than by searching the whole statement, so a table with a column
        or a default containing the word `strict` is not mistaken for a strict table. The
        difference decides whether every column in it is DECLARED or OBSERVED, so a loose match
        here would hand a compliance guarantee to a table that does not enforce anything.
        """
        if not ddl:
            return False
        tail = str(ddl).rsplit(")", 1)[-1]
        return "strict" in tail.lower()

    # ── the gate: NATIVE on this engine ──────────────────────────────────────────────────────
    def read_version(self) -> Tuple[int, str]:
        """`pragma schema_version`: the engine's OWN counter, bumped on every schema change.

        No Mohio-owned table. SQLite already keeps exactly the counter the server engines have to
        be given one for, and it has a property the Mohio table does not: it moves for a change
        made by ANY writer, not only for one that went through Mohio.
        """
        conn = self._connect()
        try:
            v = conn.execute("pragma schema_version").fetchone()[0]
            u = conn.execute("pragma user_version").fetchone()[0]
            return int(v), str(u)
        finally:
            conn.close()

    def ensure_version_table(self) -> None:
        """Nothing to create. Stated rather than left as a missing method.

        The interface's other adapters need a table. This one does not, and a silent no-op would
        leave a caller unsure whether the gate was set up or quietly skipped.
        """
        return None

    def bump_version(self, fingerprint: Optional[str] = None) -> Tuple[int, str]:
        """Record a managed change in `user_version`, which is the half SQLite leaves to the app.

        `schema_version` is the engine's and must not be written by hand. `user_version` is
        explicitly the application's to use, so a managed change bumps that, and drift_check
        reads both: the engine's counter for "something changed", ours for "we did it".
        """
        conn = self._connect()
        try:
            cur = conn.execute("pragma user_version").fetchone()[0]
            conn.execute(f"pragma user_version = {int(cur) + 1}")
            conn.commit()
        finally:
            conn.close()
        return self.read_version()

    def drift_check(self, known_schema_version: int, known_fingerprint: str) -> Dict:
        """Two tiers here as well, and the cheap one is genuinely free.

        Tier 1 is `pragma schema_version`, which the engine maintains. Unlike the server family's
        Mohio-owned row it catches an out-of-band change too, so `out_of_band` here means
        "the engine saw a change we did not make", which is a stronger statement than the same
        field means on MySQL.
        """
        schema_version, user_version = self.read_version()
        live_fp = self.introspect().semantic_fingerprint()
        return {
            "schema_version": schema_version,
            "user_version": user_version,
            "version_moved": schema_version != known_schema_version,
            "live_fingerprint": live_fp,
            "fingerprint_moved": live_fp != known_fingerprint,
            "native_gate": True,
        }
