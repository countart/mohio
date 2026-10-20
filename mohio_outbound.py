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
                   "person", "started_at", "finished_at", "fingerprint"]


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
            "started_at": started_at, "finished_at": "", "fingerprint": fingerprint or ""},
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
