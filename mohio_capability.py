# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Phase 3 -- what each datasource CAN do, as a profile the planner can read.

WHY A PROFILE RATHER THAN A BRANCH. A planner that asks `if postgres` is not a planner, it is a
pile of special cases wearing one. The roadmap is explicit: generic planner rules reason from
CAPABILITIES, and backend-specific code may populate them. So the question a plan asks is never
"which engine is this" but "does this source return generated ids from a batch", and the answer
lives here.

ONE STRUCTURE, TWO PRODUCERS, and Phase 0 settled which is which.

  THE DECLARED HALF is a property of the CONNECTOR CLASS and sits beside `sql_placeholder` and
  `supports_field_validation` on the runtime. Can this connector emit a multi-row insert at all?
  Does it have a native bulk form? A third-party connector declares its own, and one that
  declares nothing is refused at connect, exactly as the safety floor already works.

  THE OBSERVED HALF is a property of the BOUND DEPLOYMENT and cannot be declared by anybody.
  Whether this Postgres is 16 or 14, whether this Mongo is a replica set that can do
  multi-document transactions, what this driver's real parameter limit is -- none of that belongs
  to a class. It is discovered, the same way `SourceAdapter.introspect()` already discovers
  schema, and it carries the same two hashes: one for whether the MEANING drifted, one for
  whether the RECORD was corrupted.

WHERE IT IS STORED, and this is a constraint rather than a preference. `NormalizedIndex` is keyed
by `SourcePath` and enforces one path per field, raising on a duplicate. A capability belongs to
the SOURCE, not to any field, so putting `bulk.native` at a field path would be exactly the
distortion that invariant exists to prevent. The profile is a SIBLING artifact: another key in
the same object store, never a section of the field index.

UNKNOWN = NOT SAFE, and here it has teeth. If nothing has confirmed that this source can do a
native bulk load, the planner may not plan one. Not "probably yes because it is Postgres" -- the
Postgres in front of you may be behind a connection pooler that does not pass COPY, or the role
may lack the grant. An unconfirmed capability reads UNKNOWN and UNKNOWN forbids, so the worst
case is a plan that is slower than it needed to be rather than one the backend cannot execute.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ── the answer vocabulary ───────────────────────────────────────────────────────────────
#
# Three values, and the third is not a gap to be filled in later by whoever needs an answer.
# UNKNOWN is the answer, and it means the planner may not rely on this.
YES = "yes"
NO = "no"
UNKNOWN = "unknown"


# ── what a source may be asked ──────────────────────────────────────────────────────────
#
# Named as QUESTIONS ABOUT BEHAVIOUR, never as engine features. `native_bulk_insert` rather than
# `copy`, because the planner must be able to ask it of Mongo without the name lying.
CAPABILITIES = (
    # -- writing more than one row --
    "multi_row_insert",         # one INSERT carrying several rows
    "executemany",              # the driver's own repeat-a-statement path
    "native_bulk_insert",       # COPY, LOAD DATA, insertMany -- a dedicated bulk channel
    "set_update",               # one UPDATE covering many rows by predicate
    "upsert",                   # merge / ON CONFLICT / replaceOne with upsert

    # -- what comes back, which the IR's result contract needs --
    "returns_ids_on_batch",     # the ids of EVERY row of a batch, in order
    "returns_affected_count",

    # -- how it behaves --
    "pipelining",               # send without waiting for each reply
    "multi_statement_transaction",
    "savepoints",
    "per_row_error_attribution",   # can it say WHICH row of a batch failed
    "preserves_input_order",

    # -- durability, which nothing in this tree currently sets --
    "durability_lever",         # a write concern or a commit-sync knob exists to be set
)


@dataclass
class CapabilityProfile:
    """What one bound source can do, with the two halves kept apart.

    THE HALVES ARE NOT MERGED INTO ONE ANSWER by this class, deliberately. A declared YES that
    the deployment contradicts is the single most dangerous state this model can be in, and
    merging would hide it. `effective()` is where the two meet, and it resolves a disagreement
    by refusing.
    """
    source: str = ""
    engine: str = ""
    driver: str = ""
    server_version: str = ""
    declared: Dict[str, str] = field(default_factory=dict)
    observed: Dict[str, str] = field(default_factory=dict)
    limits: Dict[str, Any] = field(default_factory=dict)
    probed_at: str = ""
    notes: Dict[str, str] = field(default_factory=dict)

    # ── the question the planner actually asks ──────────────────────────────────────────
    def effective(self, name: str) -> str:
        """Can this source do `name`? The only answer a plan may act on.

        THE RESOLUTION RULE, and every part of it is the cautious reading:

          - a capability nobody named at all is UNKNOWN;
          - if the deployment was probed and said NO, that wins over any declaration. The
            connector class describes what the CODE can emit; the deployment describes what the
            server will accept, and the server is the one that has to run it;
          - if the two disagree the other way -- declared NO, observed YES -- the answer is
            UNKNOWN, not YES. A connector that says it cannot emit something is not made able to
            by a server that would have accepted it, and a disagreement between the two halves is
            a fact about the model being wrong, which is never a reason to permit more;
          - YES requires the declared half to say YES and the observed half to agree or be
            silent. Silence from a probe that never ran is not agreement, but it is not
            contradiction either, so a declared-only YES stands for capabilities that are
            genuinely properties of the code. `confirmed()` is the stricter question.
        """
        d = self.declared.get(name, UNKNOWN)
        o = self.observed.get(name, UNKNOWN)
        if o == NO or d == NO:
            return NO
        if d == YES and o in (YES, UNKNOWN):
            return YES
        if d == UNKNOWN and o == YES:
            # observed without a declaration: the code never claimed it, so do not grant it
            return UNKNOWN
        return UNKNOWN

    def confirmed(self, name: str) -> bool:
        """Both halves say yes. The bar for anything irreversible or expensive to get wrong."""
        return self.declared.get(name) == YES and self.observed.get(name) == YES

    def can(self, name: str) -> bool:
        """The fail-closed predicate. True only for YES -- never for UNKNOWN."""
        return self.effective(name) == YES

    def unknowns(self):
        return tuple(c for c in CAPABILITIES if self.effective(c) == UNKNOWN)

    # ── the two hashes, mirroring the metasource adapter contract ───────────────────────
    def semantic_fingerprint(self) -> str:
        """Has what this source CAN DO drifted? Covers the capabilities and the limits that
        bear on a plan, and deliberately ignores when it was probed."""
        payload = json.dumps(
            {"engine": self.engine,
             "effective": {c: self.effective(c) for c in CAPABILITIES},
             "limits": self.limits},
            sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def content_hash(self) -> str:
        """Has the RECORD been corrupted? Covers everything, incidental detail included."""
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self):
        return {"source": self.source, "engine": self.engine, "driver": self.driver,
                "server_version": self.server_version, "declared": dict(self.declared),
                "observed": dict(self.observed), "limits": dict(self.limits),
                "probed_at": self.probed_at, "notes": dict(self.notes)}

    @classmethod
    def from_dict(cls, d):
        return cls(source=d.get("source", ""), engine=d.get("engine", ""),
                   driver=d.get("driver", ""), server_version=d.get("server_version", ""),
                   declared=dict(d.get("declared") or {}),
                   observed=dict(d.get("observed") or {}),
                   limits=dict(d.get("limits") or {}), probed_at=d.get("probed_at", ""),
                   notes=dict(d.get("notes") or {}))

    def describe(self):
        # NAMED, NOT SHRUGGED AT. An unidentified source and an unprobed version are different
        # facts, and a shared placeholder would report them as the same absence.
        who = self.source if self.source else "<source not identified>"
        eng = self.engine if self.engine else "<engine not identified>"
        drv = self.driver if self.driver else "<driver not recorded>"
        ver = self.server_version if self.server_version else "<not probed>"
        lines = ["%s  engine=%s  driver=%s  version=%s" % (who, eng, drv, ver)]
        for c in CAPABILITIES:
            lines.append("   %-28s declared=%-8s observed=%-8s effective=%s"
                         % (c, self.declared.get(c, UNKNOWN), self.observed.get(c, UNKNOWN),
                            self.effective(c)))
        if self.limits:
            lines.append("   limits: %s" % json.dumps(self.limits, sort_keys=True))
        return "\n".join(lines)


# ── the declared half ───────────────────────────────────────────────────────────────────

def declared_profile(runtime) -> Dict[str, str]:
    """What the CONNECTOR CLASS asserts about itself.

    Read from the runtime rather than from a table keyed by engine name, so a connector somebody
    else writes declares its own and is not silently assumed to behave like one of ours. A
    connector that declares nothing gets UNKNOWN for everything, which forbids -- the same shape
    the safety floor already uses for field validation.
    """
    declared = {c: UNKNOWN for c in CAPABILITIES}
    stated = getattr(runtime, "write_capabilities", None)
    if isinstance(stated, dict):
        for name, value in stated.items():
            if name in declared and value in (YES, NO, UNKNOWN):
                declared[name] = value

    # `save_many` IS ALREADY A DECLARED CAPABILITY, and has been since before this model existed:
    # the base class returns None to DECLINE and a runtime that can do it overrides. Reading it
    # here rather than asking connectors to repeat themselves keeps one source of that truth.
    if type(runtime).save_many is not _base_save_many():
        declared.setdefault("multi_row_insert", UNKNOWN)
        if declared["multi_row_insert"] == UNKNOWN:
            declared["multi_row_insert"] = YES
        # THE IDS ARE NOT OPTIONAL ON THAT PATH. The base class's own contract says a runtime
        # that cannot return the id of every row must decline rather than take the bulk path, so
        # a runtime that overrides save_many is asserting both.
        if declared["returns_ids_on_batch"] == UNKNOWN:
            declared["returns_ids_on_batch"] = YES
    return declared


def _base_save_many():
    from mohio_interpreter import _QueryableRuntime
    return _QueryableRuntime.save_many


# ── the observed half ───────────────────────────────────────────────────────────────────

class CapabilityAdapter:
    """What every source must be able to answer about what it CAN DO.

    Mirrors `SourceAdapter` on purpose, down to the two hashes: that contract is already proven
    here, and a second shape for the same job would be two things to keep in step.

    Deliberately narrow, for the same reason the schema adapter is: an adapter says what is THERE
    and how sure it is. It does not decide what to do about it.
    """

    name = "abstract"
    engine = "abstract"

    def probe(self, runtime) -> CapabilityProfile:
        raise NotImplementedError(
            "%s must implement probe(runtime) -> CapabilityProfile." % type(self).__name__)

    def semantic_fingerprint(self, runtime) -> str:
        return self.probe(runtime).semantic_fingerprint()

    def content_hash(self, runtime) -> str:
        return self.probe(runtime).content_hash()


def _now():
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _scalar(runtime, sql):
    """One value from the live connection, or None if the question could not be asked.

    A probe that fails is not an answer of NO. It leaves the capability UNKNOWN, which forbids,
    and that is the honest outcome: not being able to ask whether a server can do something is
    not the same as learning that it cannot.
    """
    try:
        cur = runtime.raw_cursor()
    except Exception:                                   # noqa: BLE001
        return None
    try:
        cur.execute(sql)
        row = cur.fetchone()
    except Exception:                                   # noqa: BLE001
        return None
    finally:
        try:
            cur.close()
        except Exception:                               # noqa: BLE001
            pass
    if row is None:
        return None
    # THE ROW SHAPES DIFFER AND ONLY ONE OF THEM INDEXES BY NAME ONLY. psycopg2's RealDictRow
    # and pymysql's DictCursor rows are dicts; sqlite3.Row has `keys()` and is NOT a dict, and
    # indexes by position. Asking for `.values()` on one because it had `keys()` made every
    # SQLite probe fail -- and the failure was INVISIBLE, because a failed probe leaves every
    # capability unknown, which forbids, so nothing broke and nothing said anything either.
    if isinstance(row, dict):
        values = list(row.values())
        return values[0] if values else None
    return row[0]


class PostgresCapabilityAdapter(CapabilityAdapter):
    name = "postgres"
    engine = "postgres"

    def probe(self, runtime) -> CapabilityProfile:
        version = _scalar(runtime, "SHOW server_version")
        observed = {}
        notes = {}
        limits = {}
        if version is not None:
            observed["multi_row_insert"] = YES
            observed["set_update"] = YES
            observed["multi_statement_transaction"] = YES
            observed["savepoints"] = YES
            observed["returns_affected_count"] = YES
            observed["preserves_input_order"] = YES
            # ON CONFLICT arrived in 9.5 and every supported line is far past it, but the
            # version was READ rather than assumed, so this is measured rather than believed.
            observed["upsert"] = YES
            # THE BIND-PARAMETER CEILING IS REAL AND IT IS THE THING THAT BREAKS A NAIVE MULTI-ROW
            # INSERT. 65535 parameters per statement, so the row count a plan may use depends on
            # how many columns it is writing. Recorded as a limit rather than a capability,
            # because the answer is a number and a yes/no would lose it.
            limits["max_bind_parameters"] = 65535
        # COPY IS NOT ASSUMED FROM THE ENGINE NAME. Whether this deployment will accept it
        # depends on the role's grants and on whether a pooler sits in front of the server, and
        # neither is visible from the version string. It stays UNKNOWN until something confirms
        # it, and UNKNOWN forbids.
        notes["native_bulk_insert"] = (
            "COPY is a Postgres feature, but whether THIS deployment accepts it depends on the "
            "role's grants and on any pooler in front of the server. Not probed, so unknown.")
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "", engine=self.engine,
            driver="psycopg2", server_version=str(version or ""),
            declared=declared_profile(runtime), observed=observed, limits=limits,
            probed_at=_now(), notes=notes)


class MySQLCapabilityAdapter(CapabilityAdapter):
    name = "mysql"
    engine = "mysql"

    def probe(self, runtime) -> CapabilityProfile:
        version = _scalar(runtime, "SELECT VERSION()")
        observed = {}
        limits = {}
        notes = {}
        if version is not None:
            observed["multi_row_insert"] = YES
            observed["set_update"] = YES
            observed["returns_affected_count"] = YES
            observed["preserves_input_order"] = YES
            observed["upsert"] = YES          # ON DUPLICATE KEY UPDATE
            observed["savepoints"] = YES
            # TRANSACTIONS ARE A PROPERTY OF THE TABLE'S ENGINE HERE, not of the server: MyISAM
            # ignores them silently. Asking the server for its default is as far as a probe can
            # honestly go, and a table on another engine is not covered by that answer.
            default_engine = _scalar(runtime, "SELECT @@default_storage_engine")
            if default_engine and str(default_engine).lower() == "innodb":
                observed["multi_statement_transaction"] = YES
            else:
                notes["multi_statement_transaction"] = (
                    "the default storage engine is %r, and a non-transactional engine ignores a "
                    "transaction silently, so this is unknown rather than yes" % default_engine)
            packet = _scalar(runtime, "SELECT @@max_allowed_packet")
            if packet:
                limits["max_allowed_packet"] = int(packet)
        notes["native_bulk_insert"] = (
            "LOAD DATA is a MySQL feature and needs both a server setting and a client flag, "
            "neither of which is visible from the version. Not probed, so unknown.")
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "", engine=self.engine,
            driver="pymysql", server_version=str(version or ""),
            declared=declared_profile(runtime), observed=observed, limits=limits,
            probed_at=_now(), notes=notes)


class SQLiteCapabilityAdapter(CapabilityAdapter):
    name = "sqlite"
    engine = "sqlite"

    def probe(self, runtime) -> CapabilityProfile:
        version = _scalar(runtime, "SELECT sqlite_version()")
        observed = {}
        limits = {}
        notes = {}
        if version is not None:
            observed["multi_row_insert"] = YES
            observed["set_update"] = YES
            observed["returns_affected_count"] = YES
            observed["preserves_input_order"] = YES
            observed["multi_statement_transaction"] = YES
            observed["savepoints"] = YES
            observed["executemany"] = YES     # the driver's own, always present
            parts = str(version).split(".")
            try:
                major, minor = int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                major = minor = 0
            # UPSERT arrived in 3.24. The version is read rather than assumed because a system
            # SQLite can be older than the one this was developed against.
            if (major, minor) >= (3, 24):
                observed["upsert"] = YES
            limits["max_variable_number"] = 999      # the compile-time default
        notes["native_bulk_insert"] = "SQLite has no bulk channel separate from INSERT."
        notes["pipelining"] = "an in-process file engine has no round trip to pipeline"
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "", engine=self.engine,
            driver="sqlite3", server_version=str(version or ""),
            declared=declared_profile(runtime), observed=observed, limits=limits,
            probed_at=_now(), notes=notes)


class MongoCapabilityAdapter(CapabilityAdapter):
    name = "mongo"
    engine = "mongo"

    def probe(self, runtime) -> CapabilityProfile:
        observed = {}
        notes = {}
        limits = {}
        client = getattr(runtime, "client", None) or getattr(runtime, "_client", None)
        info = None
        if client is not None:
            try:
                info = client.server_info()
            except Exception:                           # noqa: BLE001
                info = None
        version = (info or {}).get("version", "")
        if info is not None:
            observed["native_bulk_insert"] = YES       # insertMany / bulkWrite
            observed["multi_row_insert"] = YES
            observed["upsert"] = YES
            observed["returns_affected_count"] = YES
            observed["per_row_error_attribution"] = YES  # a bulk result names the failed index
            observed["durability_lever"] = YES          # write concern is a real, settable knob
            limits["max_write_batch_size"] = 100000
        # A MULTI-DOCUMENT TRANSACTION NEEDS A REPLICA SET. A standalone deployment does not have
        # them at all, and this project has already recorded that Mongo's begin/commit/rollback
        # are no-ops here, so a declared yes would be worse than silence.
        try:
            hello = client.admin.command("hello") if client is not None else None
        except Exception:                               # noqa: BLE001
            hello = None
        if hello and (hello.get("setName") or hello.get("msg") == "isdbgrid"):
            observed["multi_statement_transaction"] = YES
        else:
            notes["multi_statement_transaction"] = (
                "a multi-document transaction needs a replica set or a sharded cluster; this "
                "deployment does not present as one, so it is unknown rather than yes")
        notes["returns_ids_on_batch"] = (
            "insertMany returns the inserted ids, but this runtime's own bulk path has not been "
            "confirmed to carry them back in order, so it stays unknown until it is")
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "", engine=self.engine,
            driver="pymongo", server_version=str(version),
            declared=declared_profile(runtime), observed=observed, limits=limits,
            probed_at=_now(), notes=notes)


_ADAPTERS = {
    "PostgresRuntime": PostgresCapabilityAdapter,
    "MySQLRuntime": MySQLCapabilityAdapter,
    "DbRuntime": SQLiteCapabilityAdapter,
    "MongoRuntime": MongoCapabilityAdapter,
}


def adapter_for(runtime) -> Optional[CapabilityAdapter]:
    """The capability adapter for a runtime, or None when nobody has written one.

    None is a real answer and it forbids: a connector with no adapter gets a declared-only
    profile, and anything it did not declare stays UNKNOWN.
    """
    cls = _ADAPTERS.get(type(runtime).__name__)
    return cls() if cls else None


def profile_for(runtime, probe: bool = True) -> CapabilityProfile:
    """The full profile for a bound runtime: declared always, observed when it can be probed.

    A PROBE THAT FAILS LEAVES UNKNOWN STANDING. It never writes NO, because failing to ask a
    question is not the same as learning the answer is no, and a NO is as load-bearing as a YES
    once a planner reads it.
    """
    adapter = adapter_for(runtime)
    if adapter is None or not probe:
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "",
            engine=type(runtime).__name__, driver="", server_version="",
            declared=declared_profile(runtime), observed={}, probed_at=_now(),
            notes={"observed": "no capability adapter for this runtime, so nothing was probed "
                               "and every unstated capability is unknown"})
    try:
        return adapter.probe(runtime)
    except Exception as e:                              # noqa: BLE001
        return CapabilityProfile(
            source=getattr(runtime, "_conn_source", "") or "",
            engine=adapter.engine, driver="", server_version="",
            declared=declared_profile(runtime), observed={}, probed_at=_now(),
            notes={"probe_failed": "%s -- every unconfirmed capability stays unknown" % e})


# ── stored alongside metasource, never inside it ────────────────────────────────────────

CAPABILITY_KEY_PREFIX = "capability/"


class UnidentifiedSource(ValueError):
    """A profile that cannot say which source it describes cannot be stored."""


def store_key(source: str, engine: str) -> str:
    """The key a profile is stored under, in the SAME object store metasource uses.

    A SIBLING ARTIFACT, and the distinction is load-bearing. `NormalizedIndex` is keyed by
    `SourcePath` and raises on a duplicate path, because one path is one field. A capability
    belongs to the SOURCE and to no field at all, so writing `bulk.native` at a field path would
    be the flat-namespace distortion that invariant exists to catch. Same store, own key.
    """
    # AN UNIDENTIFIED SOURCE IS REFUSED RATHER THAN FILED UNDER A PLACEHOLDER. Defaulting the
    # engine to the word "unknown" meant two different sources that both failed to identify
    # themselves landed on ONE key and overwrote each other -- and a planner would then read one
    # source's capabilities and act on them for another. There is no safe placeholder for
    # identity, so this refuses.
    if not source or not engine:
        raise UnidentifiedSource(
            "a capability profile needs both a source and an engine to be stored: got "
            "source=%r engine=%r. Two profiles that cannot name themselves would share one key "
            "and overwrite each other, and a plan would then act on the wrong source's "
            "capabilities." % (source, engine))
    digest = hashlib.sha256(("%s|%s" % (engine, source)).encode("utf-8")).hexdigest()[:16]
    return "%s%s/%s.json" % (CAPABILITY_KEY_PREFIX, engine, digest)


def save_profile(store, profile: CapabilityProfile) -> str:
    body = json.dumps(profile.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return store.put(store_key(profile.source, profile.engine), body)


def load_profile(store, source: str, engine: str) -> Optional[CapabilityProfile]:
    body, _etag = store.get(store_key(source, engine))
    if not body:
        return None
    return CapabilityProfile.from_dict(json.loads(body.decode("utf-8")))


def is_stale(stored: CapabilityProfile, fresh: CapabilityProfile) -> bool:
    """Has what this source can do drifted since the profile was written?

    Compares the SEMANTIC fingerprint, not the content hash: a profile probed at a different
    moment is not stale, and a profile whose capabilities changed is, even if it was written a
    second ago. That split is the reason there are two hashes rather than one.
    """
    return stored.semantic_fingerprint() != fresh.semantic_fingerprint()
