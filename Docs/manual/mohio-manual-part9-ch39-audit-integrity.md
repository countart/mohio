<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 39: Audit evidence integrity

The audit trail is a hash-chained, append-only record kept in your own database. This chapter is what the runtime
actually does with it.

**The hash chain.** Each record carries the previous record's hash and its own (`prev_hash`, `entry_hash`). Each
hash is computed over the encoding version, the previous hash, and the record's canonical payload, so removing or
altering any record breaks every hash after it. The current encoding is `mohio-audit-2`, under which a
present-but-empty value and an absent value hash the SAME, so the two cannot produce diverging chains for what is
the same state; records written under the earlier encoding keep verifying.

**What each record carries (who, when, principal, agent).** A record stamps the timestamp (`ts`), the acting
PERSON and AGENT (`session.person` and `session.agent`, set at the authentication boundary and stamped
automatically, see Chapter 29), the sector in force, and the session. So the trail answers who did a thing, when,
on whose authority, and whether an agent acted for them, without the program passing any of it by hand.

**Verification.** `mio audit verify <db>` recomputes every chain and reports, per log, the entry count and whether
the chain is intact, with the head hash. Verified live:

```
mio audit verify app.db
  v  data_audit_log  1 entries . chain intact
     head 35b2f6167d6edbf52f...
```

`mio audit head` reads the current head; `mio audit verify-anchors <db> --anchors <file>` compares the live chain
against externally published chain heads.

**How tampering and gaps are detected and surfaced.** An altered or removed record breaks the chain, and `verify`
reports the break rather than passing clean. A chain alone cannot catch truncation or wholesale replacement by
someone who can rewrite the whole trail; FORK PREVENTION is the anchor check (`verify-anchors`), which compares the
live head against heads you published elsewhere, so a truncated or replaced trail no longer matches its anchor. A
separate completeness check reads the delivery envelope beside the chain, so a record that committed its evidence
but never reached the trail is reported OWED rather than silently clean.

**How a legitimate correction is represented.** The trail is append-only and hash-chained, so a record is NEVER
edited in place. A correction is represented by APPENDING a new record; the original stays, and the chain over
both tells the true story. There is NO dedicated correction or amendment construct today (an explicit "this record
supersedes that one" link), tracked as `T1-AUDIT-CORRECTION-MODEL`. Erasure for a data-subject request is a
separate path: `cm.purge` writes a non-reversible tombstone rather than deleting the chain (see
`Docs/guides/compliance.md`).

## Audit sink grade: tamper-evident is not tamper-proof

A hash chain stored in the same database as the data is TAMPER-EVIDENT, not TAMPER-PROOF, and the difference
matters for a regulated deployment. The chain detects tampering by anyone who CANNOT rewrite it: remove or alter a
record and every hash after it breaks, so `mio audit verify` reports the break. It does NOT by itself stop someone
who CAN rewrite the whole trail. With write access to the audit tables, an attacker edits a row, recomputes every
hash after it, and verification passes clean. The chain is strong against loss, partial corruption and a careless
insider; it is not by itself proof against a determined one.

So the sink the trail is written to has a GRADE. A local SQLite file is tamper-evident but NOT append-only:
application code has write access to the file, so it can alter an audit row, and nothing in the file can refuse
that. When a row is altered, Mohio does not pretend the sink is more than it is. It flags the sink as below the
required grade, records the incident, audits the change, and leaves the altered row FAILING its hash, so the
tampering is visible after the fact. What it cannot do is PREVENT the change, because a file it has write access to
cannot refuse a rewrite.

Therefore a REGULATED DEPLOYMENT must bind an append-only or WORM (write-once, read-many) sink, where the storage
itself refuses a rewrite. The local file is the right grade for DEVELOPMENT and for the free tier, where
tamper-evidence is enough; it is not the right grade for a regulated production trail, and the two are different
claims. Stating "hash-chained audit trail" to an auditor as if it meant tamper-proof is the gap to avoid: it is
tamper-evident WITHIN A BOUNDARY, and the boundary is the write access to the sink. The two-axis grade model (what
the engine stores, and whether the sink is append-only) is in `Docs/guides/databases-supported.md`.

**SQLite under contention: fail-closed, with one known gap.** On SQLite the audit path is fail-closed: no governed
row is committed without its audit envelope. Under heavy single-file contention a run can still lose a
security-plane record that sits OUTSIDE that envelope; the loss is alerted loudly, never silent, and the governed
data itself is never committed un-audited. This ships as a known reliability gap, with hardening planned for a
later release. For a regulated production trail, bind an append-only or WORM sink (above) rather than a single SQLite file.

FORK PREVENTION, the half a chain cannot cover on its own, is the anchor check. `mio audit verify-anchors <db>
--anchors <file>` compares the live chain head against heads you published to a store the application cannot
rewrite, so a truncated or wholesale-replaced trail no longer matches its anchor. The verify side ships today; the
PUBLISH side (Mohio writing the head to a write-once destination on its own) is tracked, post-tag, under
`T1-AUDIT-ANCHORING`. A free user gets the chain, which is genuinely tamper-evident within its boundary; anchoring
needs a store the application cannot reach, which means credentials it does not hold, which means a platform.

## The relay backs off, and alerts once

Owed records drain on their own (Chapter 38 and `Docs/guides/audit-trail.md`): a record that cannot reach the
chain at the moment it is made is held in its durable envelope as OWED, and later writes plus a background interval
deliver it with no command. Two behaviours keep that drain from becoming its own problem during a real outage.

**It backs off.** While the sink stays unreachable, each automatic drain waits twice as long as the last, up to
`MOHIO_AUDIT_RELAY_MAX_BACKOFF_SECONDS` (900 seconds by default), rather than hammering a store that is down on
every interval. The records wait in their envelopes, so a skipped pass loses nothing, and the first pass after the
sink recovers delivers them. The base interval is `MOHIO_AUDIT_RELAY_INTERVAL_SECONDS` (30 seconds by default; `0`
turns the background loop off for a deployment that drives the drain itself). The manual `mio audit relay` command
is never held back by the backoff.

**It alerts once.** Before this, every record a down sink refused printed its own alert and raised its own
incident, on every write and every drain, so an on-call engineer got the same page hundreds of times for one
outage and a genuine second problem was buried in the flood. Now the first failure of a kind opens the outage and
says so ONCE, naming what was refused:

```
  [audit] ALERT: records owed from an earlier run could not be delivered (<reason>). They remain recoverable: `mio audit relay`. Further failures of this kind are counted under this alert, not repeated, until the audit trail accepts a record again.
```

Later failures of the same kind are counted, not repeated. The first record the sink accepts again closes the
outage with one line that carries the count:

```
  [audit] the audit trail is accepting records again (unreachable since <timestamp>; repeats counted under the alert -- <kind>: <N> more). Owed records are delivered by the next drain.
```

Two related bounds govern how long a record may stay owed: `MOHIO_AUDIT_OWED_MAX_SECONDS` (3600 by default) is when
a still-owed record becomes an incident, and `MOHIO_AUDIT_OWED_REFUSE_SECONDS` (86400 by default) is when new
regulated writes are refused rather than left to pile up behind an outage that is not clearing.

**A relay must be told which app it is.** Owed records carry the app they belong to. `mio audit relay` run without
an app identity (no `--program`, no `MOHIO_APP_ID`) does NOT deliver records that belong to an app and then report
a caught-up trail. It says so and fails, so a drain that could not know what it was draining can never look like a
clean one (verified by running, against a planted owed record):

```
1 owed audit record(s) in app.db belong to an app, and this relay was not told which app it is, so it delivered none of them.
  Run it as the app that wrote them: `mio audit relay app.db --program app.mho` (the app's own program, or any page in it), or set MOHIO_APP_ID to that app's id.
```

An identifier the relay needs is never taken from stored data either: an owed envelope's log name becomes a table
only if it is one of the program's own declared audit logs; an unrecognized name is refused before any SQL is
built, stays owed, and raises an incident, rather than creating a table named from stored data. This is the same
rule as "a table or column name is a name, never data" (the data chapter), enforced on the relay path.

**And it never reports a caught-up trail while anything is still owed.** Whatever the reason an envelope is left
owed, a delivery that failed because the sink was unreachable, or a log name the program does not declare,
`mio audit relay` counts it with its reason, reports `N owed, delivery failed: <reason>`, and exits 1 (after any
other-app note above). A drain that delivered nothing can no longer come back looking clean. A run that clears
every owed record exits 0. Every `mio audit` message shows the database URL with its password masked, so a relay
run cannot leak the audit credential.

## MongoDB and the audit trail

The audit trail reads and verifies its hash chain through SQL, and MongoDB support for it is declared but NOT
built (`T1-MONGO-AUDIT-TRAIL`). So on a MongoDB connection a regulated data write, a tagged field or a write under
a regulated sector, that needs to record a trail REFUSES at run, keeping the record as owed evidence beside the
data; other audit records are not recorded. An app with NO tagged fields and no regulated sector is unaffected.
Verified by running: the refusal is at RUN, on the regulated write, NOT at `mio check` (a Mongo app with a
`[phi]` field checks clean today). The message:

```
the audit trail '<log>' cannot be written to a MongoDB connection in this release: the trail reads and verifies its chain through SQL, and MongoDB support is declared but not built (T1-MONGO-AUDIT-TRAIL). A regulated data write is still kept as owed evidence beside the record; other audit records are not recorded. To keep a trail today, run this app on Postgres, MySQL, Oracle or SQLite.
```

**Pending (ruled 2026-10-09, not yet landed).** An app with tagged fields or a sector profile WILL refuse at
CHECK, not only at run, until the MongoDB audit trail is built, so a regulated MongoDB app is caught before
deploy rather than at its first regulated write. That check-time refusal is ruled but not landed yet; today the
refusal is runtime only, as above. Until it lands, run a regulated app on Postgres, MySQL, Oracle or SQLite,
where the trail is fully built.
