<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 38: Transactions, and what happens to the audit evidence on rollback

**A transaction is all-or-nothing.** Every write inside a `transaction` block commits together or not at all. If
any write fails partway through, every write in the block is rolled back, and the failure is returned to the
caller rather than leaving a half-written row.

```mohio
transaction
    save to db.tx_tagged
        id "row-1"
        v "first write"
    save: done
    save to db.tx_tagged
        id "row-1"
        v "duplicate id, forces a failure"
    save: done
transaction: done
```

Verified by running: the second `save` hits a duplicate key, the transaction rolls back, and reading the table
back in a fresh process shows ZERO rows persisted. The failure surfaces as a clean `409` conflict (not a 500),
which a handler can catch with `on.failure` or answer to the caller. A multi-write HANDLER is atomic the same way
without an explicit `transaction` block (see Chapter 29).

**What happens to the audit record when the transaction rolls back.** This is the part a regulated app has to
know exactly, so it was checked directly. When an audited write (a tagged field, or a write under a regulated
sector) rolls back:

- the DATA write is gone: the table holds no row for it (verified: zero rows after the rollback);
- the AUDIT trail still records the attempt, as an append-only entry that carries `outcome: rolled_back`.

Verified by reading the audit log back after the rollback above: it held one `DATA_CHANGE` record for `row-1`
whose detail included `"outcome": "rolled_back"`. The trail never claims a write that did not land. It records
that a write was ATTEMPTED and then rolled back, and the data table is correspondingly empty. The two agree.

This is the receipts principle made observable: an audit record states the OUTCOME, not the intent. A reader of
the trail sees a rolled-back attempt as exactly that, so a failed write cannot later be mistaken for a successful
one, and a successful write is never missing from the trail.

**An uploaded file whose row never lands is removed, and the removal is audited** (RULED 2026-10-05, verified by
running). An upload is written to the store just before the row that names it, so a write that then fails, or a
transaction that rolls back, would otherwise leave an object nothing references, a file nobody can reach through
the app and nobody will delete. Instead:

- when the row never commits, the object is removed and a `FILE_CLEANUP` record names its key;
- when the store refuses the removal, that is recorded as `FILE_CLEANUP_FAILED`, said aloud with the key so the
  file can be removed by hand, and it does not change the response the request already had;
- the NEGATIVE holds too: a write that LANDS keeps its object, because the committed row references it, and records
  no cleanup.

So a rolled-back or failed upload leaves no orphaned object, and an upload that succeeded keeps its file. The full
treatment of uploads and the durable store is in the files chapter.

## What else the audit trail records, and how it masks

Three behaviours a regulated app depends on, each checked by reading the audit log back after the run.

**A protected search that returns NOTHING is still audited.** A `find` or `retrieve` over a table with a `[phi]`
or `[pci]` field writes a `DATA_ACCESS` record even when it matches no rows. The record carries `count: 0`, the
protected fields it touched (`phi_fields`), and the fields it matched on (`matched_on`), hash-chained like every
other. So "someone searched the patient table for this value and got nothing" is on the record, which is exactly
the access an auditor needs to see. Verified by reading the log back after a find for a key no row holds:

```
{"event": "DATA_ACCESS", "operation": "find", "table": "patients", "count": 0,
 "phi_fields": ["patient_key"], "matched_on": ["patient_key"], "outcome": "committed", ...}
```

**`flow` and `walk` audit and MASK protected values.** A data `flow`, and a `walk` that reports each stage, records
its reads in the audit trail, and any protected value it carries is MASKED in the stage report, never shown in
plaintext. Verified by running a flow that copies a `[phi]` value from one table to another: the walk report showed
`****4680` at each stage, never the plaintext, and the destination field was stored as ciphertext
(`enc:v2:...`), sealed to the destination row it was written to.

**Scheduled work records a SYSTEM actor.** When a `mioschedule` job runs a task, the audit records the acting party
as the schedule, not a user: the session is `system` and the actor is `schedule:<name>/<task>`. So a nightly job's
reads are attributable to the schedule that ran them, distinct from any person. Verified by reading the log back
after `run mioschedule.<name> now`: the scheduled read recorded `actor='schedule:nightsweep/nightly'` and
`session='system'`, where the same program's top-level setup write recorded no actor (nobody was signed in).
