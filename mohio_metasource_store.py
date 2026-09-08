# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Portable object storage and a portable singleton lock. The deployment half of the metasource.

P6, first half. P1 through P5 answered what a source contains. This answers WHERE the answer
lives and HOW two instances agree about it, and it is the piece that has to survive the widest
range of hosts, so its only dependency is the standard library.

THE PROBLEM THIS SOLVES. A metasource held in one process is wrong twice over. On serverless
there is no process to hold it, so every invocation would pay a full introspection. On a
multi-instance staggered deploy there are several processes each holding their own copy, and
they go stale independently, so which answer you get depends on which instance answered. One
shared artifact in object storage fixes both at once: nothing to hold, one copy, one truth.

COORDINATION IS THE COMPILER'S OWN, AND IT BRINGS ITS OWN LOCK. This is the load-bearing
decision. Regeneration must be a singleton (N invocations noticing drift must not all
regenerate), and the obvious way to get that is to use the host's job or queue infrastructure.
Mohio cannot: it runs on any host, and a coordination mechanism that assumes one host's
scheduler is not portable, it is that host's. Checked on the platform side rather than assumed,
the one lock available there is tied to a single deployment and exists nowhere else. So the lock
is built here, against the storage the artifact ALREADY lives in, because every deployment has
one of those by definition.

WHY NOT boto3. Five verbs are needed: GET, PUT, HEAD, DELETE, LIST. SigV4 is about sixty lines
of hmac and hashlib, all of it standard library, and a compiler that can coordinate without
anything installed is worth more than sixty lines saved. A serverless bundle that must carry an
SDK before the compiler can agree with itself has a portability problem, not a convenience one.

THREE THINGS WERE MEASURED AGAINST A REAL S3-COMPATIBLE STORE, not assumed, and two of them
changed the design:

  1. `PUT If-None-Match: *`  creates only if absent. Second writer gets 412. This is the atomic
     acquire, and it exists wherever object storage exists.
  2. `PUT If-Match: <etag>`  overwrites only if the object is still exactly the one we read.
     Second writer with the same etag gets 412. This is a genuine compare-and-swap, and it is
     what makes STEALING an expired lock safe: two acquirers that both see the same stale lock
     both try the swap, and exactly one wins.
  3. `DELETE If-Match: <etag>` IS NOT HONOURED. Measured: a delete carrying a deliberately wrong
     etag returned 204 and the object was gone. That is a precondition silently ignored, which
     is this project's highest-severity class arriving from inside a vendor's API, and it would
     have been invisible in a test that only ever passed the right etag.

     CONSEQUENCE, and it shapes the whole protocol: THE LOCK IS NEVER DELETED. Releasing is a
     compare-and-swap that writes a `free` state, so every state transition rides on the one
     conditional the store actually enforces. A release built on a conditional delete would have
     looked correct, passed a single-threaded test, and silently released somebody else's lock.

THE LEASE IS MEASURED ON THE STORE'S CLOCK, NEVER THE CALLER'S. A crashed holder must not
deadlock regeneration forever, so a lock older than its lease may be stolen. If each instance
judged that by its own wall clock, two instances a minute apart would disagree about whether a
lock had expired, and the one running fast would steal a live lock. Every S3 response carries
`Date` (the store's now) and every object carries `Last-Modified` (the store's record of when
this lock was written). Both come from one clock, so the comparison is between two readings of
the same clock and instance skew cannot enter. The holder's own timestamp is recorded in the
body as information, and is never what expiry is decided on.

FENCING. A holder that stalls past its lease, gets stolen from, and then wakes up still believes
it holds the lock. Every acquisition carries a fence number that only increases, and a write
guarded by a fence is refused if the fence has moved. So the woken holder cannot publish over
the newer regeneration, and finds out it lost rather than silently overwriting.
"""

from __future__ import annotations

import datetime
import email.utils
import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, List, Optional, Tuple

from mohio_metasource import MetasourceError

# Lock states. A lock object exists from its first acquisition onward and changes state in place,
# because the store enforces a conditional PUT and does not enforce a conditional DELETE.
HELD = "held"
FREE = "free"

DEFAULT_LEASE_SECONDS = 60


class StoreError(MetasourceError):
    """The storage layer could not do what was asked. Never used for a lost race."""


class LockLost(MetasourceError):
    """This holder no longer holds the lock: its fence moved, so someone else has it."""


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup, and it is not defensive padding.

    Measured on a real store: the same value comes back as `ETag` on a PUT and `Etag` on a HEAD.
    An exact-case lookup returns None for one of them, and None is the value that would make the
    conditional write silently unconditional, so the etag would be dropped exactly where it is
    load bearing.
    """
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _http_date(value: str) -> datetime.datetime:
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


# ── the object store ─────────────────────────────────────────────────────────────────────────
class ObjectStore:
    """What the coordinator needs from storage, and nothing more.

    Narrow on purpose. The two conditionals are the whole reason this interface exists: any
    store that can offer create-if-absent and swap-if-unchanged can host the lock, and a store
    that cannot is not usable for coordination no matter how good its other verbs are.
    """

    name = "abstract"

    def get(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        """(body, etag), or (None, None) if it is not there."""
        raise NotImplementedError

    def put(self, key: str, body: bytes, content_type: str = "application/json") -> str:
        raise NotImplementedError

    def put_if_absent(self, key: str, body: bytes) -> Optional[str]:
        """Create only if absent. Returns the new etag, or None if someone else got there."""
        raise NotImplementedError

    def put_if_match(self, key: str, body: bytes, etag: str) -> Optional[str]:
        """Overwrite only if the object is still the one with `etag`. None means it moved."""
        raise NotImplementedError

    def stat(self, key: str) -> Optional[Dict]:
        """{etag, last_modified, store_now} or None. Both times are on the STORE'S clock."""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def list(self, prefix: str = "") -> List[str]:
        raise NotImplementedError


class S3ObjectStore(ObjectStore):
    """Any S3-compatible store: Tigris, AWS S3, R2, MinIO, Ceph. Standard library only.

    Nothing here is vendor-specific. An endpoint, a bucket, a region and a key pair are the whole
    configuration, and they come from the environment so the same build runs against a developer's
    MinIO and a production Tigris bucket without a code change.
    """

    name = "s3"

    def __init__(self, endpoint: Optional[str] = None, bucket: Optional[str] = None,
                 region: Optional[str] = None, access_key: Optional[str] = None,
                 secret_key: Optional[str] = None, timeout: float = 30.0):
        self.endpoint = (endpoint or os.environ.get("MOHIO_STORE_ENDPOINT") or "").rstrip("/")
        self.bucket = bucket or os.environ.get("MOHIO_STORE_BUCKET") or ""
        # THE REGION HAS NO DEFAULT, and that is deliberate rather than strict. It is part of the
        # SigV4 signing scope, so a guessed one does not produce a message about the region: it
        # produces SignatureDoesNotMatch, which reads as a bad key and sends whoever is debugging
        # it to rotate credentials that were fine. Tigris and R2 want "auto" and AWS wants a real
        # region name, so there is no value that is right for both to guess.
        self.region = region or os.environ.get("MOHIO_STORE_REGION") or ""
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID") or ""
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
        self.timeout = timeout
        missing = [n for n, v in (("MOHIO_STORE_ENDPOINT", self.endpoint),
                                  ("MOHIO_STORE_BUCKET", self.bucket),
                                  ("MOHIO_STORE_REGION", self.region),
                                  ("AWS_ACCESS_KEY_ID", self.access_key),
                                  ("AWS_SECRET_ACCESS_KEY", self.secret_key)) if not v]
        if missing:
            raise StoreError(
                "S3ObjectStore is not configured: " + ", ".join(missing) + " is not set. "
                "A store client that fell back to a default endpoint would coordinate a "
                "deployment against a bucket nobody chose, and one that guessed a region would "
                "fail the signature instead of naming the region. Tigris and R2 use "
                "MOHIO_STORE_REGION=auto; AWS S3 uses the bucket's real region.")

    # ── signing ──────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _signing_key(self, datestamp: str) -> bytes:
        k = self._sign(("AWS4" + self.secret_key).encode("utf-8"), datestamp)
        k = self._sign(k, self.region)
        k = self._sign(k, "s3")
        return self._sign(k, "aws4_request")

    def _request(self, method: str, key: str, body: bytes = b"",
                 headers: Optional[Dict[str, str]] = None,
                 query: str = "") -> Tuple[int, bytes, Dict[str, str]]:
        headers = dict(headers or {})
        host = urllib.parse.urlsplit(self.endpoint).netloc
        path = "/" + self.bucket + ("/" + key if key else "")
        canonical_uri = urllib.parse.quote(path, safe="/~")
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()

        headers["host"] = host
        headers["x-amz-date"] = amzdate
        headers["x-amz-content-sha256"] = payload_hash
        lower = {k.lower(): str(v).strip() for k, v in headers.items()}
        signed_names = sorted(lower)
        canonical_headers = "".join(f"{n}:{lower[n]}\n" for n in signed_names)
        signed_headers = ";".join(signed_names)
        canonical = (f"{method}\n{canonical_uri}\n{query}\n{canonical_headers}\n"
                     f"{signed_headers}\n{payload_hash}")
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        to_sign = (f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
                   f"{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}")
        signature = hmac.new(self._signing_key(datestamp), to_sign.encode("utf-8"),
                             hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")

        url = self.endpoint + path + (("?" + query) if query else "")
        request = urllib.request.Request(url, data=(body if body else None), method=method)
        for name, value in headers.items():
            if name.lower() != "host":
                request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as e:
            # An HTTP error code is an ANSWER here, not a failure: 404 means absent and 412 means
            # the precondition held against us, and both are the outcomes this layer exists to
            # report. A transport error is a different thing and is raised below.
            return e.code, e.read(), dict(e.headers)
        except urllib.error.URLError as e:
            raise StoreError(f"{method} {url} could not reach the store: {e.reason}") from e

    # ── the verbs ────────────────────────────────────────────────────────────────────────────
    def get(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        status, body, headers = self._request("GET", key)
        if status == 404:
            return None, None
        if status != 200:
            raise StoreError(f"GET {key} returned {status}: {body[:300]!r}")
        return body, _header(headers, "etag")

    def put(self, key: str, body: bytes, content_type: str = "application/json") -> str:
        status, response, headers = self._request(
            "PUT", key, body, {"Content-Type": content_type})
        if status not in (200, 201):
            raise StoreError(f"PUT {key} returned {status}: {response[:300]!r}")
        return _header(headers, "etag") or ""

    def put_if_absent(self, key: str, body: bytes) -> Optional[str]:
        """The atomic acquire. 412 means another writer created it first, which is not an error."""
        status, response, headers = self._request(
            "PUT", key, body, {"Content-Type": "application/json", "If-None-Match": "*"})
        if status == 412:
            return None
        if status not in (200, 201):
            raise StoreError(f"conditional create of {key} returned {status}: {response[:300]!r}")
        return _header(headers, "etag") or ""

    def put_if_match(self, key: str, body: bytes, etag: str) -> Optional[str]:
        """The compare-and-swap. 412 means the object moved under us, which is not an error."""
        status, response, headers = self._request(
            "PUT", key, body, {"Content-Type": "application/json", "If-Match": etag})
        if status == 412:
            return None
        if status not in (200, 201):
            raise StoreError(f"conditional swap of {key} returned {status}: {response[:300]!r}")
        return _header(headers, "etag") or ""

    def stat(self, key: str) -> Optional[Dict]:
        status, body, headers = self._request("HEAD", key)
        if status == 404:
            return None
        if status != 200:
            raise StoreError(f"HEAD {key} returned {status}: {body[:300]!r}")
        last_modified = _header(headers, "last-modified")
        store_date = _header(headers, "date")
        if not last_modified or not store_date:
            raise StoreError(
                f"HEAD {key} returned neither Last-Modified nor Date, so the lease has no clock "
                f"to be measured on. Falling back to this machine's clock would let two "
                f"instances disagree about whether a lock has expired.")
        return {"etag": _header(headers, "etag"),
                "last_modified": _http_date(last_modified),
                "store_now": _http_date(store_date)}

    def delete(self, key: str) -> None:
        status, body, _ = self._request("DELETE", key)
        if status not in (200, 204, 404):
            raise StoreError(f"DELETE {key} returned {status}: {body[:300]!r}")

    def list(self, prefix: str = "") -> List[str]:
        import re
        query = "list-type=2&max-keys=1000"
        if prefix:
            query += "&prefix=" + urllib.parse.quote(prefix, safe="")
        status, body, _ = self._request("GET", "", query=query)
        if status != 200:
            raise StoreError(f"LIST returned {status}: {body[:300]!r}")
        return re.findall(r"<Key>([^<]*)</Key>", body.decode("utf-8", "replace"))


class LocalObjectStore(ObjectStore):
    """The same interface over a directory, for a single-host deployment with no bucket.

    Not a mock and not a test double: it is the real store for a deployment that has a filesystem
    and no object storage, and its conditionals are real ones. `os.open` with O_CREAT|O_EXCL is
    the filesystem's own atomic create-if-absent, which is the same guarantee If-None-Match gives
    over HTTP. It coordinates processes on one machine, which is exactly as far as a filesystem
    reaches, and it is not offered as an answer for multiple machines.
    """

    name = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        safe = key.replace("\\", "/").strip("/")
        if not safe or ".." in safe.split("/"):
            raise StoreError(f"{key!r} is not a usable object key.")
        full = os.path.join(self.root, *safe.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    @staticmethod
    def _etag(body: bytes) -> str:
        return '"' + hashlib.md5(body).hexdigest() + '"'

    def get(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        path = self._path(key)
        if not os.path.exists(path):
            return None, None
        with open(path, "rb") as f:
            body = f.read()
        return body, self._etag(body)

    def put(self, key: str, body: bytes, content_type: str = "application/json") -> str:
        # WRITTEN ASIDE, THEN MOVED INTO PLACE. Opening the real path "wb" truncates it before a
        # byte is written, so a reader arriving in that window sees an EMPTY object -- and the
        # lock protocol above reads these as JSON, so it got "Expecting value: line 1 column 1"
        # and refused, which is a lock that cannot be read rather than a lock that is free or
        # held. `os.replace` is atomic on POSIX and on Windows, so a reader sees either the old
        # object or the new one and never a half-written one. The S3 backend has always had this
        # property, because a PUT is atomic at the server; the local one claims to coordinate
        # processes on one machine and needs it to be true here too.
        path = self._path(key)
        tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
        return self._etag(body)

    def put_if_absent(self, key: str, body: bytes) -> Optional[str]:
        # SAFE FALLBACK, and the one place in this file where an exception is an ANSWER rather
        # than a fault. O_EXCL exists to report that the object was already there, and "it was
        # already there" is precisely what this method is asking; None is the interface's word
        # for it. The catch is the specific FileExistsError, not a bare except, so a permission
        # error or a full disk still raises. Raising instead would be the wrong behaviour, not a
        # louder one: it would turn losing a race into a crash on every instance but the winner.
        try:
            fd = os.open(self._path(key), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        return self._etag(body)

    def put_if_match(self, key: str, body: bytes, etag: str) -> Optional[str]:
        # A lock file, so the compare and the swap are one operation rather than two that another
        # process can slip between. Without it this reads the etag, another process swaps, and
        # this one overwrites anyway -- a compare-and-swap that does not compare.
        path = self._path(key)
        guard = path + ".swap"
        # SAFE FALLBACK, same reason as put_if_absent above: another process holds the guard, so
        # this caller's swap did not land, which is exactly what None means here.
        try:
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        try:
            os.close(fd)
            current, current_etag = self.get(key)
            if current is None or current_etag != etag:
                return None
            with open(path, "wb") as f:
                f.write(body)
            return self._etag(body)
        finally:
            os.remove(guard)

    def stat(self, key: str) -> Optional[Dict]:
        path = self._path(key)
        if not os.path.exists(path):
            return None
        body, etag = self.get(key)
        # ONE CLOCK, same rule as S3: the file's mtime and "now" both come from this machine,
        # which is the only machine a filesystem store coordinates.
        return {"etag": etag,
                "last_modified": datetime.datetime.fromtimestamp(
                    os.path.getmtime(path), datetime.timezone.utc),
                "store_now": datetime.datetime.now(datetime.timezone.utc)}

    def delete(self, key: str) -> None:
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)

    def list(self, prefix: str = "") -> List[str]:
        found: List[str] = []
        for base, _dirs, files in os.walk(self.root):
            for name in files:
                rel = os.path.relpath(os.path.join(base, name), self.root).replace("\\", "/")
                if rel.startswith(prefix) and not rel.endswith(".swap"):
                    found.append(rel)
        return sorted(found)


# ── the lock ─────────────────────────────────────────────────────────────────────────────────
class LockHandle:
    """Proof of a held lock, and the fence that makes the proof checkable later.

    `fence` only ever increases. A holder that stalls past its lease and wakes up still has a
    handle, and the handle is still truthful about WHEN it was acquired: what it no longer is is
    current, and the fence is how that gets noticed instead of assumed.
    """

    def __init__(self, key: str, holder: str, fence: int, etag: str,
                 acquired_at: datetime.datetime, lease_seconds: int, stolen: bool = False):
        self.key = key
        self.holder = holder
        self.fence = fence
        self.etag = etag
        self.acquired_at = acquired_at
        self.lease_seconds = lease_seconds
        self.stolen = stolen        # True when this acquisition took over an expired lease

    def __repr__(self) -> str:
        return (f"<LockHandle {self.key} holder={self.holder} fence={self.fence}"
                f"{' stolen' if self.stolen else ''}>")


class ObjectStoreLock:
    """A singleton lock with a lease, on top of the two conditionals every object store has.

    THE PROTOCOL, in full, because each step exists to close a specific race:

      absent            -> create-if-absent. Two racers, one 200 and one 412. The winner holds.
      present and free  -> swap-if-unchanged. Two racers see the same etag, one swap lands.
      present, held, expired -> the same swap. The etag both stealers read is the EXPIRED lock,
                            so the first steal changes it and the second is refused. This is why
                            the steal is a compare-and-swap and not a delete followed by a
                            create: between a delete and a create, a third party can acquire
                            cleanly and then be deleted by a fourth still working from the old
                            reading.
      present, held, live    -> not available. Say so; do not wait inside the lock.

    Releasing writes `free` through the same swap, guarded by the fence. It never deletes,
    because the conditional delete was measured to be silently ignored on a real store.
    """

    def __init__(self, store: ObjectStore, key: str,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS, holder: Optional[str] = None):
        if lease_seconds < 2:
            raise MetasourceError(
                f"A lease of {lease_seconds}s is shorter than the one-second granularity the "
                f"store records modification times at, so expiry could not be told from a "
                f"rounding difference.")
        self.store = store
        self.key = key
        self.lease_seconds = lease_seconds
        self.holder = holder or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def _body(self, state: str, fence: int) -> bytes:
        return json.dumps({
            "state": state,
            "holder": self.holder,
            "fence": fence,
            "lease_seconds": self.lease_seconds,
            # Recorded for a human reading the lock object. Expiry is NOT decided on it: it is
            # the acquirer's clock, and the whole point of the lease is that no acquirer's clock
            # is trusted.
            "holder_clock": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def read(self) -> Optional[Dict]:
        """The lock as it stands, with the store's own reading of how old it is."""
        stat = self.store.stat(self.key)
        if stat is None:
            return None
        body, etag = self.store.get(self.key)
        if body is None:
            return None
        try:
            record = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise StoreError(
                f"The lock object at {self.key} is not readable JSON ({e}). A lock whose state "
                f"cannot be read must not be guessed at: guessing free deadlocks nothing and "
                f"double-regenerates, guessing held deadlocks everything.") from e
        age = (stat["store_now"] - stat["last_modified"]).total_seconds()
        record["_etag"] = etag or stat["etag"]
        record["_age_seconds"] = age
        record["_expired"] = age > record.get("lease_seconds", self.lease_seconds)
        return record

    def acquire(self) -> Optional[LockHandle]:
        """One attempt. A handle if this caller now holds it, None if somebody else does.

        Deliberately not blocking and deliberately not retrying. An invocation that loses the
        race has something useful to do -- serve the last known good artifact -- and a caller
        that waited would turn one instance's regeneration into every instance's latency.
        """
        current = self.read()
        if current is None:
            etag = self.store.put_if_absent(self.key, self._body(HELD, 1))
            if etag is None:
                return None
            return LockHandle(self.key, self.holder, 1, etag,
                              datetime.datetime.now(datetime.timezone.utc), self.lease_seconds)

        available = (current.get("state") == FREE) or current.get("_expired")
        if not available:
            return None
        fence = int(current.get("fence", 0)) + 1
        etag = self.store.put_if_match(self.key, self._body(HELD, fence), current["_etag"])
        if etag is None:
            return None
        return LockHandle(self.key, self.holder, fence, etag,
                          datetime.datetime.now(datetime.timezone.utc), self.lease_seconds,
                          stolen=bool(current.get("_expired")
                                      and current.get("state") == HELD))

    def release(self, handle: LockHandle) -> bool:
        """Mark the lock free, but only if this holder still holds it.

        False means the lease expired and somebody else took over while this holder was working.
        That is worth knowing rather than papering over: whatever this holder was doing under the
        lock was not, at the end, done under the lock.
        """
        current = self.read()
        if current is None:
            raise StoreError(
                f"The lock object at {self.key} has disappeared. This protocol never deletes a "
                f"lock, so something outside Mohio removed it, and no holder can be sure of its "
                f"state.")
        if int(current.get("fence", 0)) != handle.fence or current.get("holder") != handle.holder:
            return False
        return self.store.put_if_match(
            self.key, self._body(FREE, handle.fence), current["_etag"]) is not None

    def still_held_by(self, handle: LockHandle) -> bool:
        """Has this handle survived? The check a long regeneration owes before it publishes."""
        current = self.read()
        if current is None:
            return False
        return (current.get("state") == HELD
                and int(current.get("fence", 0)) == handle.fence
                and current.get("holder") == handle.holder
                and not current.get("_expired"))


class DatabaseLock:
    """The same lock, in a connected database, for a deployment whose store IS the database.

    The brief's rule is that coordination rides on the storage the artifact already lives in, and
    for a deployment with no bucket that is the database. The primitives are the same two: an
    INSERT of a primary key is create-if-absent, and an UPDATE with the fence in its WHERE clause
    is compare-and-swap. Both are row-level guarantees every SQL engine already provides, so
    nothing here is engine-specific except how to ask the engine what time it is.

    THE ENGINE'S CLOCK, for the same reason the object store uses the store's. `_now_sql` is the
    one dialect difference, and it exists so that expiry is judged by the database rather than by
    whichever instance asked.
    """

    CREATE_SQL = """
        create table if not exists mohio_metasource_lock (
            lock_key      varchar(255) not null,
            holder        varchar(255) not null,
            fence         bigint not null,
            state         varchar(16) not null,
            acquired_at   double precision not null,
            lease_seconds integer not null,
            primary key (lock_key)
        )
    """

    def __init__(self, connect, key: str, dialect: str,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS, holder: Optional[str] = None):
        if dialect not in ("postgres", "mysql", "sqlite"):
            raise MetasourceError(
                f"DatabaseLock does not know how to ask a {dialect!r} engine for the time. "
                f"Every other statement it runs is portable SQL; this one is not, and guessing "
                f"would put the lease on the wrong clock.")
        self.connect = connect
        self.key = key
        self.dialect = dialect
        self.lease_seconds = lease_seconds
        self.holder = holder or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def _placeholder(self) -> str:
        return "%s" if self.dialect in ("postgres", "mysql") else "?"

    def _now_sql(self) -> str:
        if self.dialect == "postgres":
            return "select extract(epoch from now())"
        if self.dialect == "mysql":
            return "select unix_timestamp(now(3))"
        return "select strftime('%s','now') * 1.0"

    def _ddl(self) -> str:
        if self.dialect == "sqlite":
            return self.CREATE_SQL.replace("double precision", "real")
        return self.CREATE_SQL

    def ensure_table(self) -> None:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(self._ddl())
            conn.commit()
        finally:
            conn.close()

    def _now(self, cur) -> float:
        cur.execute(self._now_sql())
        return float(cur.fetchone()[0])

    def read(self) -> Optional[Dict]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            p = self._placeholder()
            cur.execute(f"select holder, fence, state, acquired_at, lease_seconds "
                        f"from mohio_metasource_lock where lock_key = {p}", (self.key,))
            row = cur.fetchone()
            if row is None:
                return None
            now = self._now(cur)
            age = now - float(row[3])
            return {"holder": row[0], "fence": int(row[1]), "state": row[2],
                    "lease_seconds": int(row[4]), "_age_seconds": age,
                    "_expired": age > float(row[4])}
        finally:
            conn.close()

    def _swap(self, conn, cur, sql, params, lost_if) -> bool:
        """A compare-and-swap UPDATE. True if this caller's swap landed.

        MARIADB DIVERGES HERE, measured against 12.3.3 and not against MySQL 8.0.46, which never
        does it: when several transactions contend for the same row, MariaDB can refuse one with
        error 1020, "record has changed since last read", instead of simply updating zero rows.
        That IS the compare-and-swap failing, and reporting it as an exception rather than as a
        lost race would turn losing a race into a crashed invocation.

        It is confirmed rather than assumed. The row is re-read and `lost_if` decides whether the
        state really did move past what this caller was working from. If it did not move, the
        error was about something else and is re-raised, so a genuine fault is never quietly
        recorded as somebody else winning.
        """
        try:
            cur.execute(sql, params)
            landed = cur.rowcount == 1
            conn.commit()
            return landed
        except Exception:
            conn.rollback()
            if lost_if(self._row(cur)):
                return False
            raise

    def _row(self, cur):
        p = self._placeholder()
        cur.execute(f"select holder, fence, state, acquired_at, lease_seconds "
                    f"from mohio_metasource_lock where lock_key = {p}", (self.key,))
        return cur.fetchone()

    def acquire(self) -> Optional[LockHandle]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            p = self._placeholder()
            now = self._now(cur)
            row = self._row(cur)
            if row is None:
                try:
                    cur.execute(
                        f"insert into mohio_metasource_lock "
                        f"(lock_key, holder, fence, state, acquired_at, lease_seconds) "
                        f"values ({p}, {p}, 1, 'held', {p}, {p})",
                        (self.key, self.holder, now, self.lease_seconds))
                    conn.commit()
                except Exception:
                    # A duplicate primary key here is the row-level create-if-absent doing its
                    # job, and that is an answer rather than a failure. Anything else is a real
                    # fault and must not be read as a lost race, so the reason is CONFIRMED by
                    # re-reading rather than inferred from the fact that something went wrong.
                    conn.rollback()
                    if self._row(cur) is None:
                        raise
                    return None
                return LockHandle(self.key, self.holder, 1, "",
                                  datetime.datetime.now(datetime.timezone.utc),
                                  self.lease_seconds)

            fence = int(row[1])
            state, acquired_at, lease = row[2], float(row[3]), int(row[4])
            expired = (now - acquired_at) > lease
            if state != FREE and not expired:
                return None
            # THE COMPARE-AND-SWAP. The fence in the WHERE clause is the compare: two instances
            # that both read fence N both try to write N+1, and the second one updates zero rows.
            won = self._swap(
                conn, cur,
                f"update mohio_metasource_lock set holder = {p}, fence = {p}, state = 'held', "
                f"acquired_at = {p}, lease_seconds = {p} "
                f"where lock_key = {p} and fence = {p}",
                (self.holder, fence + 1, now, self.lease_seconds, self.key, fence),
                lost_if=lambda row: row is not None and int(row[1]) != fence)
            if not won:
                return None
            return LockHandle(self.key, self.holder, fence + 1, "",
                              datetime.datetime.now(datetime.timezone.utc), self.lease_seconds,
                              stolen=bool(expired and state == HELD))
        finally:
            conn.close()

    def release(self, handle: LockHandle) -> bool:
        conn = self.connect()
        try:
            cur = conn.cursor()
            p = self._placeholder()
            return self._swap(
                conn, cur,
                f"update mohio_metasource_lock set state = 'free' "
                f"where lock_key = {p} and fence = {p} and holder = {p}",
                (self.key, handle.fence, handle.holder),
                # Contended here too, and the same confirmation: this holder has genuinely lost
                # the lock only if the row now carries a different fence or a different holder.
                lost_if=lambda row: row is not None and (int(row[1]) != handle.fence
                                                         or row[0] != handle.holder))
        finally:
            conn.close()

    def still_held_by(self, handle: LockHandle) -> bool:
        current = self.read()
        if current is None:
            return False
        return (current["state"] == HELD and current["fence"] == handle.fence
                and current["holder"] == handle.holder and not current["_expired"])
