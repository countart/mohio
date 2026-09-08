# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The shared metasource: one artifact, many instances, coordinated regeneration.

P6, second half. `mohio_metasource_store` supplies portable storage and a portable lock; this
supplies the policy that uses them, and the policy is where the deployment problems actually get
solved.

WHAT WAS BROKEN BY HOLDING THE METASOURCE PER INSTANCE:

  SERVERLESS. There is no long-running process to hold an index in, so either every invocation
  re-introspects the database (slow, and it hammers the source) or it works from nothing. A
  shared artifact means an invocation reads one object and starts.

  STAGGERED DEPLOY. Five instances, rolled one at a time, each with its own copy. During the
  roll they disagree, and which answer a request gets depends on which instance served it. That
  is not a cache being briefly stale, it is two different truths being served at once. One
  artifact removes the second truth: an instance is either current or behind, and being behind
  is now something it can DETECT, because the artifact carries a version and the instance knows
  which version it was built against.

THREE TRIGGERS, in order of how often they should fire:

  DEPLOY is the common one and costs nothing per request: `mio connect` introspects, publishes
  the artifact and bumps the source's version. Nothing is regenerated again until something
  changes.
  THE CHEAP GATE is per invocation and is one row read: does the source's version still match
  the artifact's? On SQLite it is the engine's own counter and catches changes Mohio never made.
  OUT-OF-BAND is what the gate finds. A migration ran somewhere else, so the artifact is behind,
  and regeneration has to happen without a deploy to hang it on. This is the case that needs the
  lock, because every concurrent invocation notices the same drift at the same moment.

STALE-CACHE-AND-SERVE, and the marking is the part that matters. When regeneration is in
progress, or the source is unreachable, or another instance holds the lock, this serves the last
known good artifact rather than failing. Serving something slightly old beats serving an error.
But a compliance claim rests on the metasource, so a window in which the answer might be behind
is RECORDED, with when it opened, why, and when it closed. An unmarked stale read is worse than
a refusal, because it is a refusal that nobody knows happened.

WHAT THIS DOES NOT DO. It does not resolve names to fields, which is P7. It holds the index and
says how current the index is, and stops there.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from mohio_metasource import (
    FieldEntry, MetasourceError, NormalizedIndex, SourcePath)
from mohio_metasource_store import LockLost, ObjectStore, StoreError

ARTIFACT_FORMAT = 1


class ArtifactCorrupt(MetasourceError):
    """The stored artifact does not hash to what it says it hashes to."""


# ── the artifact ─────────────────────────────────────────────────────────────────────────────
def serialize_index(index: NormalizedIndex, source_version: int) -> bytes:
    """A normalized index as one self-describing, self-checking object.

    `source_version` is the reading of the source's version gate at the moment this was
    generated. It travels WITH the artifact rather than being held beside it, because the pair
    is the only thing that answers "is this current": a version on its own says nothing about
    which index it belongs to.

    Both hashes are written down. The semantic fingerprint is what the cheap comparison uses,
    and the content hash is checked on every read, which is the first place in this build where
    P1's second hash earns its existence: object storage is durable, not incorruptible, and a
    truncated or half-written artifact that still parses as JSON would otherwise be adopted as
    the shape of the database.
    """
    payload = {
        "artifact_format": ARTIFACT_FORMAT,
        "source": index.source,
        "adapter": index.adapter,
        "introspected_at": index.introspected_at,
        "source_version": int(source_version),
        "semantic_fingerprint": index.semantic_fingerprint(),
        "content_hash": index.content_hash(),
        "entries": [index.entries[p].to_dict() for p in index.paths()],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def deserialize_index(blob: bytes) -> Dict:
    """{index, source_version, ...}, or a loud refusal. Never a partially-trusted index."""
    try:
        payload = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ArtifactCorrupt(
            f"The stored metasource artifact is not readable JSON ({e}). It is not treated as an "
            f"empty source: an empty index would report every field as absent, which reads as a "
            f"database that lost all its columns.") from e

    if payload.get("artifact_format") != ARTIFACT_FORMAT:
        raise ArtifactCorrupt(
            f"This artifact says format {payload.get('artifact_format')!r} and this build writes "
            f"format {ARTIFACT_FORMAT}. A newer artifact may carry fields this build would drop "
            f"on the next write, so it is refused rather than partially understood.")

    index = NormalizedIndex(source=str(payload.get("source", "")),
                            adapter=str(payload.get("adapter", "")),
                            introspected_at=str(payload.get("introspected_at", "")))
    for raw in payload.get("entries", ()):
        index.add(FieldEntry(
            path=SourcePath(raw["path"]),
            type_name=raw["type_name"],
            provenance=raw["provenance"],
            nullable=bool(raw["nullable"]),
            default_exists=bool(raw["default_exists"]),
            classification=tuple(raw.get("classification", ())),
            writable=bool(raw["writable"])))

    recomputed = index.content_hash()
    if recomputed != payload.get("content_hash"):
        raise ArtifactCorrupt(
            f"The stored artifact hashes to {recomputed[:16]} but records "
            f"{str(payload.get('content_hash'))[:16]}. The record changed after it was written, "
            f"so what it describes is not what any source was measured to contain.")
    return {"index": index,
            "source_version": int(payload.get("source_version", 0)),
            "semantic_fingerprint": payload.get("semantic_fingerprint", ""),
            "content_hash": payload.get("content_hash", "")}


# ── what a read gives back ───────────────────────────────────────────────────────────────────
@dataclass
class StaleWindow:
    """A period in which the served answer might have been behind. A compliance record.

    Open and closed, with the reason, because the compliance question is not "was it stale" but
    "between which two moments, and why". A boolean cannot answer an auditor.
    """
    opened_at: str
    reason: str
    closed_at: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def close(self) -> None:
        if self.closed_at is None:
            self.closed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class Reading:
    """The metasource as served to one caller, with how much it can be relied on."""
    index: Optional[NormalizedIndex]
    source_version: int
    stale: bool = False
    reason: str = ""
    regenerated: bool = False

    @property
    def fingerprint(self) -> str:
        return self.index.semantic_fingerprint() if self.index is not None else ""


# ── the coordinator ──────────────────────────────────────────────────────────────────────────
class SharedMetasource:
    """One artifact in shared storage, read by every instance, regenerated by one at a time.

    An instance that only READS needs a store and a source name. Regeneration additionally needs
    an adapter (to introspect) and a lock (to be the only one doing it). Separating them is not
    tidiness: the serverless read path is the hot one and must not require the ability to write.
    """

    def __init__(self, store: ObjectStore, source: str, adapter=None, lock=None,
                 prefix: str = "metasource", app_expected_version: Optional[int] = None):
        self.store = store
        self.source = source
        self.adapter = adapter
        self.lock = lock
        self.prefix = prefix.strip("/")
        # WHICH VERSION THIS INSTANCE WAS BUILT AGAINST. During a staggered deploy the old
        # instances carry the old number and the new ones the new number, and comparing an
        # instance's own number against the artifact's is how an instance discovers it is one of
        # the old ones rather than finding out from a wrong answer.
        self.app_expected_version = app_expected_version
        self.stale_windows: List[StaleWindow] = []
        self._last_good: Optional[Reading] = None

    # ── keys ─────────────────────────────────────────────────────────────────────────────────
    def _safe_source(self) -> str:
        return "".join(c if (c.isalnum() or c in "-._") else "_" for c in self.source)

    @property
    def artifact_key(self) -> str:
        return f"{self.prefix}/{self._safe_source()}/current.json"

    @property
    def lock_key(self) -> str:
        return f"{self.prefix}/{self._safe_source()}/regenerate.lock"

    # ── publishing ───────────────────────────────────────────────────────────────────────────
    def publish(self, index: NormalizedIndex, source_version: int, handle=None) -> str:
        """Write the artifact. With a lock handle, only if this holder still holds the lock.

        The fence check is what stops a holder that stalled past its lease, was stolen from, and
        then woke up from publishing over the newer regeneration. Without it that write lands
        silently and the newer index disappears with no error anywhere.
        """
        if handle is not None:
            if self.lock is None:
                raise MetasourceError(
                    "publish() was given a lock handle but this SharedMetasource has no lock, "
                    "so the handle cannot be checked and would be decorative.")
            if not self.lock.still_held_by(handle):
                raise LockLost(
                    f"This holder's lease on {self.lock_key} expired and the lock moved on, so "
                    f"the regeneration it just finished is not the current one. Refusing to "
                    f"publish over whoever holds it now.")
        return self.store.put(self.artifact_key, serialize_index(index, source_version))

    # ── reading ──────────────────────────────────────────────────────────────────────────────
    def load(self) -> Reading:
        """Read and verify the shared artifact. Raises if it is absent or corrupt."""
        blob, _etag = self.store.get(self.artifact_key)
        if blob is None:
            raise MetasourceError(
                f"There is no metasource artifact at {self.artifact_key}. It is written at "
                f"deploy time by mio connect; an instance that finds none has been deployed "
                f"without one rather than found an empty database.")
        loaded = deserialize_index(blob)
        reading = Reading(index=loaded["index"], source_version=loaded["source_version"])
        self._last_good = reading
        return reading

    def current(self) -> Reading:
        """The artifact if it can be read, otherwise the last known good, marked stale.

        A store that cannot be reached is the case this exists for. Refusing every request
        because the bucket blipped would make the shared artifact a new single point of failure,
        which is a worse trade than serving an index that is minutes old and saying so.
        """
        try:
            return self.load()
        except (StoreError, ArtifactCorrupt, MetasourceError) as e:
            if self._last_good is None:
                raise
            self._open_window(f"the shared artifact could not be read: {e}")
            return Reading(index=self._last_good.index,
                           source_version=self._last_good.source_version,
                           stale=True, reason=str(e))

    # ── the compliance marking ───────────────────────────────────────────────────────────────
    def _open_window(self, reason: str) -> StaleWindow:
        if self.stale_windows and self.stale_windows[-1].open:
            return self.stale_windows[-1]
        window = StaleWindow(
            opened_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), reason=reason)
        self.stale_windows.append(window)
        return window

    def _close_window(self) -> None:
        if self.stale_windows and self.stale_windows[-1].open:
            self.stale_windows[-1].close()

    @property
    def in_stale_window(self) -> bool:
        return bool(self.stale_windows) and self.stale_windows[-1].open

    # ── the version gate ─────────────────────────────────────────────────────────────────────
    def _source_version(self) -> Optional[int]:
        """The source's own cheap counter, or None when this adapter has no such gate.

        Only the FIRST element is read. The adapters agree on what it means -- a number that
        moves when the schema changes -- and disagree on the second, which is a fingerprint on
        Postgres and MySQL and SQLite's separate user_version. Reading the second here would be
        assuming an agreement that was never made.
        """
        reader = getattr(self.adapter, "read_version", None)
        if reader is None:
            return None
        return int(reader()[0])

    def version_report(self) -> Dict:
        """Where the three version numbers stand: this app, the artifact, the source.

        THE STAGGERED-DEPLOY DETECTOR. `app_behind_artifact` is an instance discovering that
        another instance has already deployed a newer metasource, which is exactly the condition
        that used to be invisible until it produced a wrong answer.
        """
        artifact_version: Optional[int] = None
        # WHY there is no artifact version is the useful half, and a bare None says neither of
        # the two things it could mean. "Nothing published yet" is a deployment that has not run.
        # "It will not verify" is corruption. A report whose blank cells all look the same is
        # where the second one hides behind the first.
        artifact_problem = ""
        try:
            artifact_version = self.load().source_version
        except MetasourceError as e:
            artifact_problem = f"{type(e).__name__}: {e}"
        source_version = self._source_version()
        return {
            "app_expected_version": self.app_expected_version,
            "artifact_version": artifact_version,
            "artifact_problem": artifact_problem,
            "source_version": source_version,
            "has_cheap_gate": source_version is not None,
            "artifact_behind_source": (artifact_version is not None
                                       and source_version is not None
                                       and artifact_version != source_version),
            "app_behind_artifact": (self.app_expected_version is not None
                                    and artifact_version is not None
                                    and self.app_expected_version < artifact_version),
        }

    # ── the triggers ─────────────────────────────────────────────────────────────────────────
    def deploy(self) -> Reading:
        """THE DEPLOY TRIGGER. Introspect, bump the source's version, publish. No lock needed.

        A deploy is already serialized by whatever is doing the deploying, and it is the common
        path, so it does not pay for coordination it does not need. The bump comes BEFORE the
        publish so that an instance reading between the two sees the artifact as behind and
        serves stale, rather than seeing a new artifact stamped with an old version and believing
        it is current.
        """
        if self.adapter is None:
            raise MetasourceError(
                "deploy() needs an adapter to introspect. A SharedMetasource built for reading "
                "only cannot generate the artifact it reads.")
        index = self.adapter.introspect()
        bump = getattr(self.adapter, "bump_version", None)
        if bump is not None:
            bump(index.semantic_fingerprint())
        version = self._source_version()
        self.publish(index, version if version is not None else 0)
        self._close_window()
        reading = Reading(index=index, source_version=version if version is not None else 0,
                          regenerated=True)
        self._last_good = reading
        return reading

    def ensure_current(self) -> Reading:
        """THE PER-INVOCATION PATH. Cheap gate, and regeneration by exactly one instance.

        Four outcomes, and each is reported rather than smoothed over:
          nothing moved       -> the artifact, not stale. The overwhelmingly common case, and it
                                 costs one row read.
          moved, lock won     -> this invocation regenerates and publishes. One instance does.
          moved, lock lost    -> another instance is already regenerating. Serve the last known
                                 good, marked stale, and do NOT wait: waiting would turn one
                                 instance's regeneration into every instance's latency, which is
                                 the stampede the lock exists to prevent, wearing a different
                                 hat.
          source unreachable  -> serve the last known good, marked stale.
        """
        if self.adapter is None or self.lock is None:
            raise MetasourceError(
                "ensure_current() needs both an adapter and a lock: an adapter to regenerate "
                "from, and a lock so that only one instance does.")
        try:
            source_version = self._source_version()
        except Exception as e:
            reading = self.current()
            self._open_window(f"the source could not be reached for the version gate: {e}")
            reading.stale = True
            reading.reason = f"source unreachable: {e}"
            return reading

        artifact = self.current()
        if source_version is None:
            # NO CHEAP GATE ON THIS ADAPTER. Said out loud rather than skipped: without a version
            # counter the only way to detect drift is a full introspect, and doing that silently
            # on every invocation would be an expensive surprise.
            drifted = (artifact.index is None
                       or artifact.fingerprint != self.adapter.semantic_fingerprint())
        else:
            drifted = (artifact.index is None) or (artifact.source_version != source_version)

        if not drifted:
            self._close_window()
            return artifact

        handle = self.lock.acquire()
        if handle is None:
            self._open_window("another instance holds the regeneration lock")
            return Reading(index=artifact.index, source_version=artifact.source_version,
                           stale=True, reason="another instance is regenerating")

        try:
            index = self.adapter.introspect()
            version = self._source_version()
            self.publish(index, version if version is not None else 0, handle=handle)
            self._close_window()
            reading = Reading(index=index,
                              source_version=version if version is not None else 0,
                              regenerated=True)
            self._last_good = reading
            return reading
        finally:
            self.lock.release(handle)
