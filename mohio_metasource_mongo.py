# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The Mongo source adapter. The same interface as Postgres, against a document store.

P3. Building this ALONGSIDE the Postgres adapter is the point: two engines with almost nothing in
common, implementing one interface, is what shows whether the P1 contract is real or whether it
was quietly shaped around SQL. It was not, and the two places it could have bent are worth naming
because they are where a lesser contract would have needed a special case:

  DEFAULTS. Mongo has no server-side column default. `default_exists` is therefore always False
  here, and that is an ANSWER, not a gap: the field asks "can an existing write omit this and
  still succeed", and for Mongo the answer is genuinely "not because of a default". Nothing had
  to be added to the contract to say so.

  NULLABILITY. A SQL column is NOT NULL or it is not. A Mongo field is required by a validator
  (DECLARED) or it merely happened to be present in the documents that were sampled (OBSERVED).
  The contract already carries that distinction per field, which is exactly why it did not bend:
  per-field provenance was chosen in P1 for this case, and this is the case arriving.

DECLARED FIRST, OBSERVED SECOND, and the order matters. A collection's `$jsonSchema` validator is
the only thing Mongo ENFORCES: a write that violates it is rejected by the server. So validated
fields are read first and marked DECLARED. Then the collection is sampled, and any field the
validator did not mention is added as OBSERVED, at whatever depth it was found. A collection with
a validator covering some fields and free-form documents alongside produces MIXED provenance in
ONE source, which is the case a per-source flag cannot represent at all.

DOCUMENT-SHAPE DRIFT is real and this is honest about it. The observed half is a SAMPLE: an
external writer can change the document shape with no DDL, no migration and no signal, and a
sample taken before that write cannot know. Two mitigations, and they are not equivalent:

  CHANGE STREAMS (a replica set, which Atlas always is) watch the collection and report writes as
  they happen. That is the enterprise answer, and it turns drift from "noticed at the next scan"
  into "noticed at the write".
  PERIODIC RE-SAMPLING is the fallback for a standalone mongod, where change streams do not
  exist. It is strictly worse and is offered as such rather than presented as equivalent.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mohio_metasource import (
    DECLARED, OBSERVED, FieldEntry, MetasourceError, NormalizedIndex, SourceAdapter, SourcePath)

SYSTEM_DATABASES = ("admin", "local", "config")

# BSON type names as `$jsonSchema` spells them, kept verbatim. Translating them into SQL-ish
# names would be a lie about which engine said it, and the diff compares like with like anyway.
_JSON_SCHEMA_TYPE_KEYS = ("bsonType", "type")


class MongoAdapter(SourceAdapter):
    """Introspect a live Mongo into a normalized index.

    Reads `MONGO_URL` by default, the same way PostgresAdapter reads `DATABASE_URL`. An explicit
    `uri=` always wins, so a caller never has to touch the environment to point this somewhere.
    """

    name = "mongo"

    def __init__(self, uri: Optional[str] = None, source: Optional[str] = None,
                 databases: Optional[Sequence[str]] = None, sample_size: int = 100,
                 max_depth: int = 8):
        self.uri = uri or os.environ.get("MONGO_URL") or ""
        if not self.uri:
            raise MetasourceError(
                "MongoAdapter needs a connection string: pass `uri=` or set MONGO_URL.")
        self._source = source
        self._databases = list(databases) if databases else None
        self.sample_size = sample_size
        # Documents nest without limit and a cyclic-looking shape would walk forever. A depth cap
        # is a real limit and is stated rather than hidden: fields below it are not indexed, and
        # that is visible as their absence rather than as a wrong entry.
        self.max_depth = max_depth

    # ── connection ───────────────────────────────────────────────────────────────────────────
    def _client(self):
        try:
            import pymongo
        except ImportError as e:
            raise MetasourceError(
                f"MongoAdapter needs pymongo and it could not be imported: {e}") from e
        return pymongo.MongoClient(self.uri, serverSelectionTimeoutMS=15000)

    def source_name(self, client=None) -> str:
        if self._source:
            return self._source
        own = client is None
        client = client or self._client()
        try:
            hello = client.admin.command("hello")
            # THE SOURCE NAME IS THE HEAD OF EVERY PATH, so a shared default here is a collision
            # generator: two standalone deployments both called "mongo" would make
            # `mongo.app.users.email` mean two different fields, which is the Q533 bug arriving
            # from the source side rather than from the runtime. The silent-shape ratchet caught
            # exactly that fallback in this line and was right to.
            #
            # A replica set has a name. A standalone does not, so its ADDRESS is used instead:
            # not pretty, and genuinely distinct, which is the property that matters for a key.
            set_name = hello.get("setName")
            if set_name:
                return str(set_name)
            nodes = sorted(f"{h}:{p}" for h, p in (client.nodes or ()))
            if nodes:
                return nodes[0]
            raise MetasourceError(
                "This deployment reports neither a replica set name nor a reachable node "
                "address, so there is nothing stable to name the source by. Pass `source=` to "
                "name it yourself rather than have every path keyed on a shared placeholder.")
        finally:
            if own:
                client.close()

    def supports_change_streams(self, client=None) -> bool:
        """Change streams need a replica set. A standalone mongod has none, and says so here."""
        own = client is None
        client = client or self._client()
        try:
            return bool(client.admin.command("hello").get("setName"))
        finally:
            if own:
                client.close()

    # ── the validator: the DECLARED half ─────────────────────────────────────────────────────
    @staticmethod
    def _validator_schema(db, collection: str) -> Optional[Dict]:
        for info in db.list_collections(filter={"name": collection}):
            opts = info.get("options") or {}
            validator = opts.get("validator") or {}
            return validator.get("$jsonSchema")
        return None

    @classmethod
    def _walk_schema(cls, schema: Dict, prefix: Tuple[str, ...], required_here: bool = True
                     ) -> Iterable[Tuple[Tuple[str, ...], str, bool]]:
        """(path suffix, bson type, required) for every field a $jsonSchema declares.

        Recurses into nested `properties`, so a validator that describes a sub-document produces
        DECLARED entries at full depth rather than one entry for the whole sub-document.
        """
        props = (schema or {}).get("properties") or {}
        required = set((schema or {}).get("required") or ())
        for name, spec in props.items():
            spec = spec or {}
            btype = next((spec[k] for k in _JSON_SCHEMA_TYPE_KEYS if k in spec), "unspecified")
            if isinstance(btype, list):
                btype = "|".join(str(b) for b in btype)
            here = prefix + (str(name),)
            yield here, str(btype), (name in required and required_here)
            if spec.get("properties"):
                # A nested field is only truly required if its parent is too. Saying otherwise
                # would claim the engine enforces something it does not.
                yield from cls._walk_schema(spec, here, required_here=(name in required))

    # ── sampling: the OBSERVED half ──────────────────────────────────────────────────────────
    def _walk_document(self, doc: Dict, prefix: Tuple[str, ...], depth: int,
                       out: Dict[Tuple[str, ...], str]) -> None:
        if depth > self.max_depth:
            return
        for k, v in (doc or {}).items():
            here = prefix + (str(k),)
            out.setdefault(here, self._bson_type_name(v))
            if isinstance(v, dict):
                self._walk_document(v, here, depth + 1, out)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                # An array of sub-documents indexes by the FIELD inside them, not by position.
                # `items.0.sku` would make every array element its own field, which is a fact
                # about one document rather than about the collection.
                self._walk_document(v[0], here, depth + 1, out)

    @staticmethod
    def _bson_type_name(v) -> str:
        import datetime
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "double"
        if isinstance(v, str):
            return "string"
        if isinstance(v, dict):
            return "object"
        if isinstance(v, list):
            return "array"
        if isinstance(v, (datetime.datetime, datetime.date)):
            return "date"
        return type(v).__name__

    # ── introspection ────────────────────────────────────────────────────────────────────────
    def introspect(self) -> NormalizedIndex:
        import datetime
        client = self._client()
        try:
            src = self.source_name(client)
            idx = NormalizedIndex(
                source=src, adapter=self.name,
                introspected_at=datetime.datetime.utcnow().isoformat() + "Z")
            dbs = self._databases or [d for d in client.list_database_names()
                                      if d not in SYSTEM_DATABASES]
            for dbname in dbs:
                db = client[dbname]
                for info in db.list_collections():
                    coll_name = info["name"]
                    if coll_name.startswith("system."):
                        continue
                    is_view = (info.get("type") == "view")
                    base = (src, dbname, coll_name)

                    # DECLARED FIRST. Whatever the validator covers is enforced by the server.
                    declared: Dict[Tuple[str, ...], None] = {}
                    schema = (info.get("options") or {}).get("validator", {}).get("$jsonSchema")
                    for suffix, btype, required in self._walk_schema(schema or {}, ()):
                        path = SourcePath(*base, *suffix)
                        if path in idx.entries:
                            continue
                        declared[suffix] = None
                        idx.add(FieldEntry(
                            path=path, type_name=btype, provenance=DECLARED,
                            nullable=not required,
                            default_exists=False,     # Mongo has no server-side default
                            writable=not is_view))

                    # OBSERVED SECOND. Anything the validator did not mention is a fact about
                    # the documents that were looked at, and nothing more.
                    if not is_view:
                        found: Dict[Tuple[str, ...], str] = {}
                        try:
                            for doc in db[coll_name].find({}, limit=self.sample_size):
                                self._walk_document(doc, (), 1, found)
                        except Exception:
                            found = {}
                        for suffix, btype in found.items():
                            if suffix in declared:
                                continue
                            path = SourcePath(*base, *suffix)
                            if path in idx.entries:
                                continue
                            idx.add(FieldEntry(
                                path=path, type_name=btype, provenance=OBSERVED,
                                nullable=True,        # nothing enforces its presence
                                default_exists=False,
                                writable=True))
            return idx
        finally:
            client.close()

    # ── drift ────────────────────────────────────────────────────────────────────────────────
    def watch(self, database: str, collection: str, seconds: float = 5.0) -> List[Dict]:
        """Watch a collection for writes. THE ENTERPRISE ANSWER to document-shape drift.

        A change stream turns drift from "noticed at the next scan" into "noticed at the write",
        which is the difference between a sampled index that is usually right and one that is
        current. It needs a replica set; `supports_change_streams()` says whether this deployment
        has one, and a standalone falls back to periodic re-sampling, which is strictly worse and
        is offered as such rather than as an equivalent.
        """
        import time
        client = self._client()
        try:
            if not self.supports_change_streams(client):
                raise MetasourceError(
                    "This deployment is not a replica set, so it has no change streams. Fall "
                    "back to periodic re-sampling, which sees a shape change at the next scan "
                    "rather than at the write.")
            events: List[Dict] = []
            deadline = time.time() + seconds
            with client[database][collection].watch(
                    max_await_time_ms=500, full_document="updateLookup") as stream:
                while time.time() < deadline:
                    ev = stream.try_next()
                    if ev is not None:
                        events.append({"operationType": ev.get("operationType"),
                                       "ns": ev.get("ns"),
                                       "documentKey": ev.get("documentKey")})
                    else:
                        time.sleep(0.05)
            return events
        finally:
            client.close()
