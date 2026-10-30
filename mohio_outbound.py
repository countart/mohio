# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""What a changing outbound call is known to have done, and the record of every attempt (the
outbound unknown-outcome core, RULED by Ron 2026-10-10).

A call that changes another service (a connector operation or a miohttp call that is not GET, HEAD
or OPTIONS) ends in one of four ways:

  done       a 2xx whose body the operation recognizes: it matches the operation's declared
             `returns` shape, or the operation declares none
  not done   a 4xx, a 3xx, or a request that provably never left (refused connection, a name
             that did not resolve): the service did not do it, and the program carries on
  unknown    a 2xx with a body the operation does not recognize, a 5xx, or a send that timed out
             or was dropped after it may have arrived: the service may have done it

An unknown outcome is never guessed at. The program stops safely: the client gets 202 with a
reference id, nothing runs `on.failure` (which would read as "it did not happen"), and the call is
never sent again. Every attempt is a row in `mohio_outbound_attempts` -- the key, the connector and
operation, the status, whether it needs reconciling, the handler and person, and when it started and
finished, never the payload -- written before the request leaves and finished from its outcome.

With no database the key is still sent and nothing is recorded; an unknown outcome still stops. A
sector that requires an audit grade has no store-less mode at all (refused at check).
"""
import uuid

ATTEMPTS_TABLE = "mohio_outbound_attempts"
ATTEMPT_COLUMNS = ["key", "connector", "operation", "status", "reconcile_required", "handler",
                   "person", "started_at", "finished_at", "fingerprint",
                   # the reconciler's own record of asking (mohio_reconcile): how many times, when last,
                   # and what settled it ("" until something did)
                   "reconcile_checks", "last_checked_at", "resolved_by"]


class KeyReusedError(Exception):
    """An idempotency key already recorded for a DIFFERENT request (Ron, 2026-10-13, pre-tag batch 2):
    a key names one request, so sending it with another payload would let the service answer the new
    request with the first one's result (two payouts under one app-set key, measured). Refused before
    the call leaves."""

    def __init__(self, key):
        super().__init__(key)
        self.key = key


def fingerprint(method, target, body):
    """The request a key names: its method, resolved target and body bytes as sent. A retry of the same
    request has the same fingerprint; a different payload under the same key does not."""
    import hashlib
    h = hashlib.sha256()
    h.update(str(method or "").upper().encode("utf-8") + b"\n")
    h.update(str(target or "").encode("utf-8") + b"\n")
    h.update(body if isinstance(body, (bytes, bytearray)) else str(body or "").encode("utf-8"))
    return h.hexdigest()
CHANGING = lambda method: str(method or "").upper() not in ("GET", "HEAD", "OPTIONS")  # noqa: E731


def classify(status, recognized, error=None, never_left=False):
    """'done' | 'not_done' | 'unknown' for one changing call. `status` is the HTTP status (0 when no
    response arrived), `recognized` whether a 2xx body is one the operation accepts, `error` the
    transport failure if there was one, `never_left` whether that failure proves nothing was sent."""
    if error is not None:
        return "not_done" if never_left else "unknown"
    if 200 <= status < 300:
        return "done" if recognized else "unknown"
    if status >= 500:
        return "unknown"
    return "not_done"


def new_reference():
    return uuid.uuid4().hex[:12]


def interrupted(db):
    """The attempt rows still `sending` in this database: a call whose process stopped between the
    send and the reply, or one still in flight in another process. Each is flagged for
    reconciliation (a row from an earlier build was written `false` before its outcome was known),
    and returned as (key, connector, operation, started_at). None when there is no attempts table."""
    try:
        cols = db.table_columns(ATTEMPTS_TABLE) if hasattr(db, "table_columns") else None
    except Exception:  # SILENT-OK: a catalog that cannot list the table means it was never created
        cols = None
    if not cols and not hasattr(db, "_mongo_db"):
        return None
    rows = db.find_many(ATTEMPTS_TABLE, {"status": "sending"}) or []
    for r in rows:
        if str(r.get("reconcile_required") or "") != "true":
            db.update(ATTEMPTS_TABLE, {"reconcile_required": "true"}, "key", r.get("key"))
    return [(str(r.get("key") or ""), str(r.get("connector") or ""), str(r.get("operation") or ""),
             str(r.get("started_at") or "")) for r in rows]


class AttemptLog:
    """The `mohio_outbound_attempts` rows for one interpreter.

    On Postgres, MySQL and Oracle a row is written through its OWN connection to the app's
    database, so it commits on its own: the attempt must survive whatever the request does after
    it, a rollback included, because the other service may have acted on it.

    SQLITE IS THE STATED LIMIT. It allows one writer at a time, and a request that has already
    written holds that writer, so a second connection waits and fails ("database is locked",
    measured). On SQLite the app's own connection writes the row, inside the request: an unknown
    outcome answers 202, which commits, so its row is kept; a done or not-done row inside a request
    that later answers 4xx or 5xx is rolled back with it. MongoDB has no request transaction."""

    def __init__(self, interp):
        self._interp = interp
        self._side = None

    def _store(self):
        db = getattr(self._interp, "_db", None)
        if db is None:
            return None
        if self._side is None:
            spec = getattr(self._interp, "_db_connect", None)
            driver = str(spec[0] if spec else "").lower()
            if spec is None or type(db).__name__ in ("MongoRuntime", "DbRuntime") \
                    or driver in ("sqlite", "sqlite3", ""):
                side = db
            else:
                from mohio_interpreter import _make_db_runtime
                side = _make_db_runtime(spec[0], spec[1], url=spec[2])
            side.ensure_table(ATTEMPTS_TABLE, ATTEMPT_COLUMNS, allow_new_columns=True)
            self._side = side
        return self._side

    def begin(self, key, connector, operation, handler, person, started_at, fingerprint=""):
        """Record the attempt BEFORE it is sent. False when there is no database to hold it.

        A key already recorded is a retry when its fingerprint matches (the row is reused) and a
        KeyReusedError when it does not: the same key with a different payload is refused here,
        before anything leaves."""
        store = self._store()
        if store is None:
            return False
        prior = store.find_many(ATTEMPTS_TABLE, {"key": key}, limit=1) or []
        if prior:
            recorded = str((prior[0] or {}).get("fingerprint") or "")
            if recorded and fingerprint and recorded != fingerprint:
                raise KeyReusedError(key)
            store.update(ATTEMPTS_TABLE, {"status": "sending", "reconcile_required": "true",
                                          "started_at": started_at,
                                          "fingerprint": fingerprint or recorded}, "key", key)
            return True
        # NEEDS RECONCILING UNTIL ITS OUTCOME SAYS OTHERWISE (stuck-sending, 2026-10-15). The row is
        # written before the request leaves, so "false" here was a receipt written from intent: a
        # process killed between the send and the reply left `sending` / `false`, which a reconciler
        # reading reconcile_required skips, as if the call might still complete. Only `finish`,
        # from the outcome, clears it.
        store.save(ATTEMPTS_TABLE, {
            "key": key, "connector": connector, "operation": operation, "status": "sending",
            "reconcile_required": "true", "handler": handler or "", "person": person or "",
            "started_at": started_at, "finished_at": "", "fingerprint": fingerprint or "",
            "reconcile_checks": "0", "last_checked_at": "", "resolved_by": ""},
            allow_new_columns=True)
        return True

    def finish(self, key, status, finished_at):
        """Finish the attempt from its OUTCOME: done, not_done or unknown (unknown needs reconciling)."""
        store = self._store()
        if store is None:
            return
        store.update(ATTEMPTS_TABLE, {
            "status": status, "reconcile_required": "true" if status == "unknown" else "false",
            "finished_at": finished_at}, "key", key)


# -- INBOUND: the answers an idempotent route has given (T1-ROUTE-IDEMPOTENCY, Ron 2026-10-22) --
#
# The sibling of AttemptLog, facing the other way. A `listen for` route declared
# `once per request.header "Idempotency-Key"` keeps each answer it gives under the caller's key,
# so a repeated request is answered from the record and the work is not done again.
#
# THE KEY AND THE WORK ARE ONE TRANSACTION. The slot is claimed on the app's OWN connection inside the
# request transaction, before the handler body runs, and its answer is written just before the commit.
# A refusal or failure rolls back the slot with the work, so a retry runs fresh; a success commits
# both, so a retry is answered from the record. The slot is a real unique index on every SQL engine,
# so two requests with one key at once do the work once: the second waits on the first's claim and
# then replays it, or answers 409 while it is still running. MongoDB has no request transaction, and
# is refused at check.
#
# A slot names the route, the key and the signed-in person (when there is one), so one person's key
# never replays another person's answer. The request a key names is fingerprinted; the same key with a
# different request is refused (422) before anything runs.
INBOUND_TABLE = "mohio_inbound_responses"
INBOUND_COLUMNS = ["slot", "route", "fingerprint", "answer", "created_at", "expires_at"]
DEFAULT_KEEP_SECONDS = 86400   # `keep for` when none is written: 24 hours


def inbound_slot(route, key, person):
    import hashlib
    return hashlib.sha256(("%s\n%s\n%s" % (route, person or "", key)).encode("utf-8")).hexdigest()


def request_fingerprint(route, payload):
    """The request a key names: the route and its payload, framework plumbing already removed."""
    import hashlib
    import json
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((route + "\n" + body).encode("utf-8")).hexdigest()


class AnswerNotKeptError(Exception):
    """An answer holding a value the record cannot keep exactly. Raised before the commit, so the
    request rolls back rather than keep a key whose replay would differ from what was sent."""


def _tag(v):
    import base64
    import datetime as _dt
    from decimal import Decimal
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return {"$mio": "float", "v": repr(v)}
    if isinstance(v, Decimal):
        return {"$mio": "decimal", "v": str(v)}
    if isinstance(v, _dt.datetime):
        return {"$mio": "datetime", "v": v.isoformat()}
    if isinstance(v, _dt.date):
        return {"$mio": "date", "v": v.isoformat()}
    if isinstance(v, _dt.time):
        return {"$mio": "time", "v": v.isoformat()}
    if isinstance(v, (bytes, bytearray)):
        return {"$mio": "bytes", "v": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, (list, tuple)):
        return [_tag(x) for x in v]
    if isinstance(v, dict):
        if "$mio" in v:
            raise AnswerNotKeptError("a field named `$mio`")
        return {str(k): _tag(x) for k, x in v.items()}
    raise AnswerNotKeptError(type(v).__name__)


def _untag(v):
    import base64
    import datetime as _dt
    from decimal import Decimal
    if isinstance(v, list):
        return [_untag(x) for x in v]
    if isinstance(v, dict):
        kind = v.get("$mio")
        if kind is None:
            return {k: _untag(x) for k, x in v.items()}
        raw = v.get("v")
        return {"float": float, "decimal": Decimal, "datetime": _dt.datetime.fromisoformat,
                "date": _dt.date.fromisoformat, "time": _dt.time.fromisoformat,
                "bytes": base64.b64decode}[kind](raw)
    return v


def encode_answer(answer):
    import json
    return json.dumps(_tag(answer), sort_keys=True)


def decode_answer(text):
    import json
    return _untag(json.loads(text))


class InboundAnswers:
    """The `mohio_inbound_responses` rows for one database: claimed inside the request transaction,
    finished from the answer the route gave, read back for a repeated request."""

    def __init__(self, db, unique_cols):
        self._db = db
        self._unique = unique_cols    # the interpreter's own unique registry, which every engine reads

    def ensure(self):
        """Create the table with its unique slot. Run OUTSIDE the request transaction: on MySQL a
        table change commits whatever transaction is open."""
        cols = self._unique.setdefault(INBOUND_TABLE, [])
        if "slot" not in cols:
            cols.append("slot")
        self._db.ensure_table(INBOUND_TABLE, INBOUND_COLUMNS, allow_new_columns=True)

    def lookup(self, slot):
        rows = self._db.find_many(INBOUND_TABLE, {"slot": slot}, limit=1) or []
        return rows[0] if rows else None

    @staticmethod
    def expired(row, now):
        return float(row.get("expires_at") or 0) <= now

    def claim(self, slot, route, fp, now, keep_seconds):
        self._db.save(INBOUND_TABLE, {
            "slot": slot, "route": route, "fingerprint": fp, "answer": "",
            "created_at": repr(float(now)), "expires_at": repr(float(now + keep_seconds))})

    def finish(self, slot, answer):
        """Write the answer the route gave. Only from the outcome: called with the response the client
        is about to receive, immediately before the commit that keeps it."""
        self._db.update(INBOUND_TABLE, {"answer": encode_answer(answer)}, "slot", slot)

    def drop(self, slot):
        self._db.remove(INBOUND_TABLE, "slot", slot)
