# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The durable-work reconciler: an outbound call whose outcome is unknown is resolved to what the
service says happened, never guessed, never sent again (RULED by Ron 2026-10-28; a free primitive).

Every changing call is a row in `mohio_outbound_attempts`, written before the request leaves and
carrying the Idempotency-Key it was sent with (mohio_outbound). A row still needing reconciliation
is a call that stopped between the send and the reply (`sending`) or one that ended in a way the
service may have acted on (`unknown`). A pass asks each one's RESOLVER:

  done       the service says it happened
  not done   the service says it did not
  unknown    no answer yet: the row stays pending and the next pass asks again

THE LOOKUP RESOLVER is the one built. A changing operation names a read operation on the same
connector that looks a call up by its key (`outcome from findCharge`, whose path carries
`{{ key }}`):
  a recognized 2xx   done
  a 404              not done, but only once the consistency window has passed since the call
                     (MOHIO_RECONCILE_ABSENT_SECONDS); before that a 404 may be the service not having
                     written its record yet, and concluding "not done" on it could lead a caller to
                     repeat a call that did happen
  anything else      unknown (a 5xx, an unreachable service, an unrecognized body)
A call whose operation names no lookup, or a miohttp call, has no resolver: it stays pending and is
said, never resolved by default.

THE SEAM: a second resolver, replay by key (send the identical request again under the same key to a
service that honours keys), is DEFERRED by Ron 2026-10-28, because a resend risks running the work
twice where a service's key handling is imperfect, and it needs the payload kept, sealed, until the
outcome is known. It plugs in at `resolver_for` as another OutcomeResolver; nothing else changes.

A resolution is written only to a row that still needs it (key and reconcile_required both match),
so two machines reconciling at once resolve a row once and record it once.
"""
import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from mohio_outbound import ATTEMPTS_TABLE, ATTEMPT_COLUMNS

_KEY_SLOT = re.compile(r"\{\{\s*key\s*\}\}")


class Resolution:
    """What a resolver learned: `outcome` is 'done', 'not_done', or None (still unknown)."""

    def __init__(self, outcome, detail):
        self.outcome = outcome
        self.detail = detail


class OutcomeResolver:
    """Asks the service what happened to one attempt. `how` names it on the row it resolves."""
    how = ""

    def resolve(self, attempt, now):
        raise NotImplementedError


class LookupResolver(OutcomeResolver):
    """The operation's declared lookup, called with the attempt's key in its path."""

    def __init__(self, interp, conn, connector, lookup, window):
        self.interp, self.conn, self.connector, self.lookup, self.window = interp, conn, connector, lookup, window
        self.how = "lookup %s.%s" % (connector, lookup)

    def resolve(self, attempt, now):
        from mohio_interpreter import _http_open, Context
        op = self.conn['operations'][self.lookup]
        key = str(attempt.get("key") or "")
        path = _KEY_SLOT.sub(urllib.parse.quote(key, safe=""), op.get('path') or "")
        ctx = getattr(self.interp, '_declarations_ctx', None) or Context()
        try:
            _base, url, headers = self.interp._connector_target(self.conn, self.connector, self.lookup, path,
                                                                ctx, None)
        except Exception as e:
            return Resolution(None, "the lookup could not be prepared (%s)" % str(e).splitlines()[0][:200])
        timeout = float(op.get('timeout') or self.conn.get('timeout') or 30)
        try:
            with _http_open(urllib.request.Request(url, headers=headers, method="GET"), timeout, "GET",
                            url) as resp:
                status, raw = resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            status, raw = e.code, ""
        except Exception as e:
            return Resolution(None, "%s could not reach the service (%s)" % (self.lookup, str(e)[:200]))
        if 200 <= status < 300:
            if not _is_json(raw):
                # A body that is not JSON settles it only for a lookup that declares no `returns`
                # shape: the rule a call's own reply follows (_outbound_body_recognized).
                if op.get('returns'):
                    return Resolution(None, "%s answered %d with a body that is not JSON"
                                      % (self.lookup, status))
                return Resolution("done", "%s answered %d: the service has it" % (self.lookup, status))
            parsed = json.loads(raw)
            if self.interp._outbound_body_recognized(op, parsed, ctx):
                return Resolution("done", "%s answered %d: the service has it" % (self.lookup, status))
            return Resolution(None, "%s answered %d with a body it does not recognize" % (self.lookup, status))
        if status == 404:
            age = _age_seconds(self.interp, attempt.get("started_at"), now)
            if age is not None and age >= self.window:
                return Resolution("not_done", "%s answered 404 %ds after the call, past the %ds a service may "
                                  "take to record it" % (self.lookup, age, self.window))
            return Resolution(None, "%s answered 404 %s after the call, inside the %ds a service may take to "
                              "record it" % (self.lookup, "%ds" % age if age is not None else "an unknown time",
                                             self.window))
        return Resolution(None, "%s answered %d" % (self.lookup, status))


def _is_json(text):
    """Whether `text` is a JSON document (an answer the lookup can read field by field)."""
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _age_seconds(interp, started_at, now):
    """Seconds since the call started, or None when its start cannot be read (the caller then
    concludes nothing from a 404, and says so)."""
    t = interp._parse_datetime(str(started_at or ""))
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return int((now - t).total_seconds())


def resolver_for(interp, connector, operation):
    """The resolver for one attempt, or None when its operation declares none (it stays pending)."""
    conn = (getattr(interp, '_connectors', None) or {}).get(connector)
    op = ((conn or {}).get('operations') or {}).get(operation) if conn else None
    lookup = (op or {}).get('outcome_from')
    if not lookup or lookup not in conn['operations']:
        return None
    return LookupResolver(interp, conn, connector, lookup, interp._reconcile_absent_window())


def _store(interp):
    """The attempts store a pass reads and writes, and whether the pass owns it (closes it after).
    None when nothing durable holds attempts: no database, or one that lives in memory only."""
    spec = getattr(interp, '_db_connect', None)
    db = getattr(interp, '_db', None)
    if db is None or spec is None:
        return None, False
    driver = str(spec[0] or "").lower()
    if driver in ("sqlite", "sqlite3", ""):
        if str(spec[1] or "") in (":memory:", ""):
            return None, False
        return db, False
    from mohio_interpreter import _make_db_runtime
    return _make_db_runtime(spec[0], spec[1], url=spec[2]), True


def reconcile_pass(interp, now=None):
    """Ask about every call still needing reconciliation. Returns a summary:
    {'done': [...], 'not_done': [...], 'pending': [(key, connector, operation, why)], 'no_store': bool}."""
    from mohio_interpreter import _now_utc
    now = now or _now_utc()
    summary = {"done": [], "not_done": [], "pending": [], "no_store": False}
    store, owned = _store(interp)
    if store is None:
        summary["no_store"] = True
        return summary
    try:
        try:
            cols = store.table_columns(ATTEMPTS_TABLE) if hasattr(store, "table_columns") else None
        except Exception:  # SILENT-OK: a catalog that cannot list the table means no call was ever recorded
            cols = None
        if not cols and not hasattr(store, "_mongo_db"):
            return summary
        store.ensure_table(ATTEMPTS_TABLE, ATTEMPT_COLUMNS, allow_new_columns=True)
        stamp = now.isoformat()
        # The record of a resolution goes where every other record goes, through the program's own
        # connection, named as the reconciler's work rather than any person's.
        from mohio_interpreter import Context
        base = getattr(interp, '_declarations_ctx', None)
        actx = base.child() if base is not None else Context()
        actx._system_actor = "outbound reconciler"
        for row in store.find_many(ATTEMPTS_TABLE, {"reconcile_required": "true"}) or []:
            key, connector, operation = (str(row.get(c) or "") for c in ("key", "connector", "operation"))
            resolver = resolver_for(interp, connector, operation)
            if str(row.get("status") or "") == "sending" and resolver is not None:
                # STILL IN FLIGHT, PERHAPS. A call that started less than its timeout ago may yet get
                # its own reply, which records its outcome; asking now could race that record.
                op = resolver.conn['operations'].get(operation) or {}
                in_flight = float(op.get('timeout') or resolver.conn.get('timeout') or 30) + 5
                age = _age_seconds(interp, row.get("started_at"), now)
                if age is not None and age < in_flight:
                    summary["pending"].append((key, connector, operation, "still within its own timeout"))
                    continue
            if resolver is None:
                res = Resolution(None, "its operation names no `outcome from` lookup, so nothing can ask "
                                       "the service; it stays pending until someone does")
            else:
                res = resolver.resolve(row, now)
            # A row from before this column existed has no count: it was never checked.
            prior = row.get("reconcile_checks")
            checks = str((0 if prior in (None, "") else int(prior)) + 1)
            match = {"key": key, "reconcile_required": "true"}
            if res.outcome in ("done", "not_done"):
                n = store.update_multi(ATTEMPTS_TABLE, {
                    "status": res.outcome, "reconcile_required": "false", "finished_at": stamp,
                    "last_checked_at": stamp, "reconcile_checks": checks, "resolved_by": resolver.how},
                    match) or 0
                if n:
                    # RECORDED FROM THE OUTCOME, ONCE: only the pass whose write landed records it.
                    interp._audit_event("data_audit_log", {
                        "event": "outbound_resolved", "key": key, "connector": connector,
                        "operation": operation, "outcome": res.outcome, "resolved_by": resolver.how,
                        "meaning": res.detail}, actx)
                    summary[res.outcome].append((key, connector, operation, res.detail))
            else:
                store.update_multi(ATTEMPTS_TABLE, {"last_checked_at": stamp, "reconcile_checks": checks},
                                   match)
                summary["pending"].append((key, connector, operation, res.detail))
        return summary
    finally:
        if owned:
            try:
                store.close()
            except Exception:  # SILENT-OK: a pass's own connection closing late changes nothing it wrote
                pass


def describe(summary):
    """The lines a pass prints: what it resolved, and what is still pending and why. Empty when
    there is nothing to say."""
    lines = []
    for outcome, word in (("done", "done"), ("not_done", "not done")):
        for key, c, o, why in summary.get(outcome) or []:
            lines.append("  [mohio.outbound] reconciled %s.%s (key %s): %s, %s." % (c, o, key, word, why))
    pend = summary.get("pending") or []
    if pend:
        shown = "; ".join("%s.%s (key %s): %s" % (c, o, k, why) for k, c, o, why in pend[:5])
        more = ", and %d more" % (len(pend) - 5) if len(pend) > 5 else ""
        lines.append("  [mohio.outbound] %d call(s) still have an unknown outcome and stay pending: %s%s."
                     % (len(pend), shown, more))
    return lines
