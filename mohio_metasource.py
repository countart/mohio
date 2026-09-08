# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The metasource CONTRACT: a normalized index of what a data source actually contains.

P1. Contract only. No real database, no resolution logic (P7), no version table (P2). What lives
here is the shape everything downstream will be written against, which is why three decisions are
baked in from the first line rather than discovered later:

1. THE KEY IS AN ARBITRARY-DEPTH ORDERED PATH, never `source.table.column`. Postgres has schemas,
   SQL Server has four-part names, Mongo nests documents inside documents. A three-segment key
   would fit exactly one of those and would have to be torn out for the second. `SourcePath` is a
   tuple of any length and the code never indexes it positionally except at the two ends, where
   the meaning is fixed: the head is the source, the tail is the field.

2. DECLARED-VERSUS-OBSERVED IS PER-FIELD PROVENANCE, not a per-source flag. One source carries
   both: a SQL column with a NOT NULL constraint is DECLARED (the database enforces it), while a
   JSON blob column sampled for its keys is OBSERVED (nothing guarantees the next row looks the
   same). A per-source flag would have to pick one and lie about the rest, and the compliance
   guarantee rides on exactly this distinction: you may claim a field is always present only when
   something enforces it.

3. THE DIFF CLASSIFIES, it does not merely detect. A change is SAFE or BREAKING, and the
   difference decides whether anything downstream is allowed to refuse. Added-nullable-with-default
   is safe; dropped-column, type-change and declared-becoming-observed are breaking. A detector
   that only says "something changed" makes every deploy a false alarm, and a system that cries
   wolf gets switched off, which is worse than not having it.

TWO HASHES, and they answer different questions:

  SEMANTIC FINGERPRINT -> has the MEANING drifted? Computed over the meaning-bearing facts only
                          (path, type, nullability, whether a default exists, classification,
                          provenance, writability). Two indexes with the same fingerprint describe
                          the same contract even if they were introspected on different days by
                          different adapter versions.
  CONTENT HASH         -> has the RECORD been corrupted? Computed over the complete serialized
                          entry, incidental detail included. Two indexes with the same semantic
                          fingerprint but different content hashes mean the same contract was
                          recorded twice and one of the recordings changed underneath you.

A fingerprint alone cannot see corruption in the parts it deliberately ignores, and a content hash
alone cannot tell a meaningless re-serialization from a real schema change. Both, deliberately.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ── Provenance ───────────────────────────────────────────────────────────────────────────────
DECLARED = "declared"   # the source ENFORCES this (SQL constraint, Mongo validator, NOT NULL)
OBSERVED = "observed"   # sampled from data; true of what was looked at, guaranteed of nothing

PROVENANCES = (DECLARED, OBSERVED)

# ── Change classification ────────────────────────────────────────────────────────────────────
SAFE = "safe"
BREAKING = "breaking"


class MetasourceError(Exception):
    """A contract violation in the index itself. Never used for a schema change."""


# ── The key ──────────────────────────────────────────────────────────────────────────────────
class SourcePath(tuple):
    """An ordered path of arbitrary depth: (source, *namespace levels, container, field).

    A tuple subclass rather than a dataclass because a path IS its segments: it has to be
    hashable, orderable and usable as a dict key without ceremony, and every one of those comes
    free from the tuple.

    Depth is NOT fixed and nothing here assumes three segments:

        ("pg", "public", "users", "email")            Postgres: schema
        ("mssql", "db", "dbo", "users", "email")      SQL Server: four-part
        ("mongo", "app", "orders", "items", "sku")    Mongo: a field inside a nested document
        ("csv", "export.csv", "email")                a flat file: no namespace at all

    The only two positions with fixed meaning are the ends: `source` is the head and `field` is
    the tail. Everything between is namespace, and how much of it there is belongs to the adapter.
    """

    def __new__(cls, *segments):
        if len(segments) == 1 and isinstance(segments[0], (list, tuple)):
            segments = tuple(segments[0])
        segments = tuple(str(s) for s in segments)
        if len(segments) < 2:
            raise MetasourceError(
                f"A source path needs at least a source and a field; got {segments!r}. "
                f"A one-segment path cannot say what it is pointing at.")
        if any(not s for s in segments):
            raise MetasourceError(
                f"A source path has an empty segment: {segments!r}. An empty segment makes two "
                f"different paths compare equal, which is how a field silently borrows another "
                f"field's classification.")
        return super().__new__(cls, segments)

    @property
    def source(self) -> str:
        return self[0]

    @property
    def field(self) -> str:
        return self[-1]

    @property
    def container(self) -> str:
        """The thing the field lives in: the last segment before the field.

        For a two-segment path there is no container, and this says so with an empty string
        rather than returning the source and letting a caller mistake one for the other.
        """
        return self[-2] if len(self) >= 3 else ""

    @property
    def namespace(self) -> Tuple[str, ...]:
        """Everything between the source and the container. Empty for a flat source."""
        return tuple(self[1:-2]) if len(self) >= 4 else ()

    def __str__(self) -> str:
        return ".".join(self)


# ── The entry ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FieldEntry:
    """One field, and everything the contract knows about it.

    `provenance` is the load-bearing one. `type_name` and `nullable` mean something different
    depending on it: DECLARED means the source enforces them, OBSERVED means an adapter looked at
    some rows and this is what it saw. A compliance claim may only rest on DECLARED.
    """
    path: SourcePath
    type_name: str
    provenance: str                        # DECLARED | OBSERVED
    nullable: bool = True
    default_exists: bool = False
    classification: Tuple[str, ...] = ()   # ('pii',), ('phi', 'pci'), () -- sorted, deduped
    writable: bool = True

    def __post_init__(self):
        if self.provenance not in PROVENANCES:
            raise MetasourceError(
                f"{self.path}: provenance is {self.provenance!r}, which is neither "
                f"{DECLARED!r} nor {OBSERVED!r}. A field whose provenance is unknown cannot "
                f"carry a compliance claim, so there is no third value to default to.")
        if not self.type_name:
            raise MetasourceError(f"{self.path}: a field entry needs a type name.")
        object.__setattr__(self, "classification",
                           tuple(sorted({str(c) for c in (self.classification or ())})))

    # The meaning-bearing facts, in a fixed order. Everything the fingerprint covers and nothing
    # else, so a change here is a deliberate change to what "the same contract" means.
    def semantic_tuple(self) -> Tuple:
        return (str(self.path), self.type_name, self.provenance, self.nullable,
                self.default_exists, self.classification, self.writable)

    def to_dict(self) -> Dict:
        # Built by hand rather than with `asdict`. `asdict` recurses into the SourcePath tuple
        # and tries to rebuild it as `type(obj)(<generator>)`, which reaches `SourcePath.__new__`
        # with one generator argument and trips its own "a path needs at least two segments"
        # guard. The guard is right; using a generic serializer on a type with a validating
        # constructor is what was wrong.
        return {
            "path": list(self.path),
            "type_name": self.type_name,
            "provenance": self.provenance,
            "nullable": self.nullable,
            "default_exists": self.default_exists,
            "classification": list(self.classification),
            "writable": self.writable,
        }


# ── The index ────────────────────────────────────────────────────────────────────────────────
@dataclass
class NormalizedIndex:
    """Every field a source contains, keyed by path, with both hashes."""
    source: str
    entries: Dict[SourcePath, FieldEntry] = field(default_factory=dict)
    adapter: str = ""
    introspected_at: str = ""      # incidental: in the content hash, NOT in the fingerprint

    def add(self, entry: FieldEntry) -> None:
        if entry.path in self.entries:
            raise MetasourceError(
                f"{entry.path} is already in the index. One path is one field; two entries for "
                f"one path is the flat-namespace bug arriving from the adapter side.")
        self.entries[entry.path] = entry

    def paths(self):
        return sorted(self.entries, key=lambda p: tuple(p))

    def semantic_fingerprint(self) -> str:
        """Has the MEANING drifted? Covers the contract facts, ignores when it was read."""
        payload = json.dumps([list(self.entries[p].semantic_tuple()) for p in self.paths()],
                             sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def content_hash(self) -> str:
        """Has the RECORD been corrupted? Covers everything, incidental detail included."""
        payload = json.dumps(
            {"source": self.source, "adapter": self.adapter,
             "introspected_at": self.introspected_at,
             "entries": [self.entries[p].to_dict() for p in self.paths()]},
            sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── The diff ─────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Change:
    """One column-granular change, already classified."""
    path: SourcePath
    kind: str            # 'added' | 'dropped' | 'changed'
    severity: str        # SAFE | BREAKING
    reason: str
    before: Optional[FieldEntry] = None
    after: Optional[FieldEntry] = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind} {self.path}: {self.reason}"


def _classify_added(after: FieldEntry) -> Tuple[str, str]:
    # A new field that existing writes can ignore is safe. One they cannot is not: an existing
    # INSERT that never mentions this column now fails, which is a break caused by an addition.
    if after.nullable or after.default_exists:
        return SAFE, ("added, and existing writes can omit it "
                      f"({'nullable' if after.nullable else 'has a default'})")
    return BREAKING, ("added NOT NULL with no default, so every existing write that does not "
                      "mention it now fails")


def _classify_changed(before: FieldEntry, after: FieldEntry) -> List[Tuple[str, str]]:
    """Every difference between two entries for one path, each already classified."""
    out: List[Tuple[str, str]] = []
    if before.type_name != after.type_name:
        out.append((BREAKING, f"type changed {before.type_name} -> {after.type_name}"))
    if before.nullable != after.nullable:
        if after.nullable:
            out.append((SAFE, "became nullable, which existing readers already tolerate"))
        else:
            out.append((BREAKING, "became NOT NULL, so existing writes that omit it now fail"))
    if before.default_exists != after.default_exists:
        if after.default_exists:
            out.append((SAFE, "gained a default"))
        else:
            out.append((BREAKING, "lost its default, so writes relying on it now fail"))
    if before.provenance != after.provenance:
        if after.provenance == OBSERVED:
            # THE COMPLIANCE ONE. Nothing about the data changed; what changed is that the source
            # stopped GUARANTEEING it. A claim resting on this field is no longer supportable, and
            # that is exactly the kind of change a schema-diff that only watches types sails past.
            out.append((BREAKING, "provenance fell from declared to observed: the source no "
                                  "longer enforces this, so a guarantee resting on it is gone"))
        else:
            out.append((SAFE, "provenance rose from observed to declared: now enforced"))
    if before.classification != after.classification:
        # In EITHER direction. Gaining a tag means data that was being handled as ordinary is
        # regulated; losing one means protection stopped being applied to data that had it.
        out.append((BREAKING,
                    f"classification changed {list(before.classification)} -> "
                    f"{list(after.classification)}"))
    if before.writable != after.writable:
        if after.writable:
            out.append((SAFE, "became writable"))
        else:
            out.append((BREAKING, "became read-only, so existing writes now fail"))
    return out


def diff_index(before: NormalizedIndex, after: NormalizedIndex) -> List[Change]:
    """Column-granular three-way set diff: added, dropped, changed. Each change classified.

    THREE-WAY means the three set relations between two keyed indexes: present only in `after`
    (added), present only in `before` (dropped), present in both with different content (changed).
    A path present in both and identical produces nothing, which is what keeps a quiet deploy
    quiet.
    """
    changes: List[Change] = []
    before_paths, after_paths = set(before.entries), set(after.entries)

    for p in sorted(after_paths - before_paths, key=tuple):
        entry = after.entries[p]
        sev, why = _classify_added(entry)
        changes.append(Change(path=p, kind="added", severity=sev, reason=why, after=entry))

    for p in sorted(before_paths - after_paths, key=tuple):
        entry = before.entries[p]
        # Always breaking. This layer cannot see whether any code reads the column, and guessing
        # that nothing does is the assumption that turns a silent deploy into a 3am outage.
        changes.append(Change(path=p, kind="dropped", severity=BREAKING,
                              reason="dropped, and this layer cannot know whether code reads it",
                              before=entry))

    for p in sorted(before_paths & after_paths, key=tuple):
        b, a = before.entries[p], after.entries[p]
        for sev, why in _classify_changed(b, a):
            changes.append(Change(path=p, kind="changed", severity=sev, reason=why,
                                  before=b, after=a))
    return changes


def breaking(changes: Iterable[Change]) -> List[Change]:
    return [c for c in changes if c.severity == BREAKING]


# ── The adapter interface ────────────────────────────────────────────────────────────────────
class SourceAdapter:
    """What every source must be able to answer. Introspection in, normalized index out.

    Deliberately narrow. An adapter's whole job is to say what is THERE and how sure it is; it
    does not resolve, reconcile or version, because those need to work identically across every
    source and so cannot live inside any one of them.
    """

    name: str = "abstract"

    def introspect(self) -> NormalizedIndex:
        raise NotImplementedError(
            f"{type(self).__name__} must implement introspect() -> NormalizedIndex.")

    def semantic_fingerprint(self) -> str:
        return self.introspect().semantic_fingerprint()

    def content_hash(self) -> str:
        return self.introspect().content_hash()


class MockAdapter(SourceAdapter):
    """A source held in memory, for testing the contract without a database.

    It is a real adapter, not a stub: it returns a genuine NormalizedIndex through the same
    interface a Postgres or Mongo adapter will, so a test written against it is a test of the
    contract rather than of the mock.
    """

    name = "mock"

    def __init__(self, source: str = "mock", fields: Optional[Sequence[Dict]] = None,
                 introspected_at: str = "2026-01-01T00:00:00Z"):
        self.source = source
        self.introspected_at = introspected_at
        self._fields: List[Dict] = list(fields or [])

    def add_field(self, path, type_name, provenance=DECLARED, nullable=True,
                  default_exists=False, classification=(), writable=True):
        self._fields.append(dict(path=SourcePath(path), type_name=type_name,
                                 provenance=provenance, nullable=nullable,
                                 default_exists=default_exists,
                                 classification=tuple(classification), writable=writable))
        return self

    def drop_field(self, path):
        p = SourcePath(path)
        before = len(self._fields)
        self._fields = [f for f in self._fields if f["path"] != p]
        if len(self._fields) == before:
            raise MetasourceError(f"{p} is not in this mock source, so it cannot be dropped.")
        return self

    def change_field(self, path, **kw):
        p = SourcePath(path)
        for f in self._fields:
            if f["path"] == p:
                f.update(kw)
                return self
        raise MetasourceError(f"{p} is not in this mock source, so it cannot be changed.")

    def introspect(self) -> NormalizedIndex:
        idx = NormalizedIndex(source=self.source, adapter=self.name,
                              introspected_at=self.introspected_at)
        for f in self._fields:
            idx.add(FieldEntry(**f))
        return idx
