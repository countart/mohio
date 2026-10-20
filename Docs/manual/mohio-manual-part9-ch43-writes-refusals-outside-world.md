<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 43: Writes, refusals and the outside world

A program writes to its own database, and sometimes it also reaches the outside world: a payment,
a transfer, an email, a call to another service. The two are not the same, and the difference is
the whole of this chapter. Mohio can take back a database write when a request refuses. It cannot
take back a call that has already left your machine. Every claim below was established by running.

### A single write stands or falls on its own

One write is atomic in itself: it either lands completely or not at all. But a refusal that comes
AFTER it does not undo it. A handler that writes one row and then answers with a non-2xx keeps that
row:

```mohio
listen for sh.Order at /order
    save to db.orders
        amount 20
    save: done
    give back [400] "refused"      // the row above is already written, and stays
listen: done
```

Verified by running: after this handler answers `[400]`, the row is in the table. So with a single
write, decide BEFORE you write. Check what you need to check, refuse if you must, and only then
save. If you cannot know until after the write, you need more than one write, which is the next
section.

### A handler that writes more than once is one transaction

A handler that makes more than one write runs in a single transaction: it commits when it answers
with a 2xx, and it rolls back when it answers with a non-2xx or when anything throws. So a handler
that writes two rows and then refuses leaves neither behind:

```mohio
listen for sh.Order at /order
    save to db.orders
        amount 20
    save: done
    save to db.lines
        ref "L-20"
    save: done
    give back [400] "refused after both writes"   // both rows roll back
listen: done
```

Verified by running, through a real POST: the same two writes followed by a `[201]` commit together,
and followed by a `[400]` both roll back, so the table holds neither. This is covered in full, with
the MongoDB replica-set note and the interaction with your own `transaction` block, in the handlers
chapter (`mohio-manual-part8-ch29-listening-for-requests.md`). The audit trail records the attempt
either way: a rolled-back write leaves an append-only record carrying `outcome: rolled_back`, never
silence (`mohio-manual-part9-ch38-transactions-and-audit.md`).

### The outside world is not part of the transaction

A connector call is not a database write. It leaves your machine, and nothing Mohio does can pull it
back. So a call that has already been made stays made, even if the request that made it refuses a
moment later and rolls back every row it wrote. Rolling back the database does not roll back the
charge.

Today an outbound call is at-most-once with no automatic recovery: if it fails or times out you
decide what to do, and you cannot assume a retry is safe, because there is no idempotency key yet to
make it safe (`mohio-manual-part9-ch40-connector-calls.md`). The consequence for this chapter is a
rule: never place an irreversible outside-world call where a later refusal would need to undo it.
Put differently, do not call the outside world in the middle of a set of writes that might still
roll back.

### The pattern today: record intent, commit, call, record the outcome

Until the reliability layer lands, the safe shape for an outside-world effect is two steps, so that a
crash at any point leaves a record to reconcile rather than a charge nobody can see:

```mohio
// Step 1: record intent and commit by answering 2xx. The pending row survives a crash.
listen for sh.Transfer at /transfer
    require role "member"
    save to db.transfers
        amount request.amount
        status "pending"
    save: done
    give back [202] "queued"
listen: done

// Step 2: find a pending transfer, call the outside world, then record the outcome.
listen for sh.Transfer at /settle
    require role "worker"
    retrieve t from db.transfers
        match status to "pending"
        on.failure
            give back [503] "transfer store unavailable"
    retrieve: done
    Pay.charge with t.amount as receipt
    update db.transfers
        status "settled"
        match id to t.id
    update: done
    give back [200] "settled"
listen: done
```

Verified clean at `mio check`. Step 1 commits a `pending` row and answers, so the intent is durable
before any call is made. Step 2, run as a follow-up request or a scheduled job, makes the call and
then records the result. If the process dies between the call and the record, the row is still
`pending`, so a later pass can see it and reconcile rather than guess. This is the outbox pattern,
built by hand; the built version is planned (below).

### What the compiler sees, and what it does not warn about yet

The compiler sees what each line does. `mio writes` lists each write in a file, normalized, so you
can read what a program will change before you run it (`mohio-manual-cli-reference.md`), and `mio
check` catches a large class of mistakes at build time.

**`mio check` now warns about the single-write-then-refuse shape (verified by running).** When a
handler makes a write that no transaction covers and can then answer with a refusal, the check says
so and teaches both fixes:

```
! This handler writes at line 6 and can still refuse at line 9 (`give back [400]`). The write is
  not inside a transaction, so the refusal answers while the row is already saved.
  Check first, then write: move the test that refuses (line 9) above the write. Or wrap the write
  and the test in one transaction, so the refusal undoes the write:
      transaction
      transaction: done
```

It fires only where it should: a multi-write handler (already one auto-transaction) and a write
wrapped in an explicit `transaction` do NOT warn, because the refusal undoes those writes. So the
single-write rule at the top of this chapter is now the compiler's to enforce, not only yours to
remember.

What it does NOT warn about yet, verified by running: an outside-world call that sits among writes
which might still roll back. That case checks clean today, so keeping an irreversible call out of a
set of writes that can refuse is still yours to hold.

The fuller model behind the warning is planned, not built:

- outside-world calls HELD until after the request commits, through an outbox, so a call is never
  made for a request that then rolls back;
- the request transaction starting at the FIRST write, automatically;
- an explicit marker for a write that must SURVIVE a refusal (an audit or security record that
  should persist even when the business write rolls back);
- a `mio check` warning for the outside-world-call-among-writes shape too.

The design is recorded in `Docs/design/DESIGN-request-atomicity.md`, and the outbound-reliability
half (idempotency keys, a retry policy that never resubmits a payment, the outbox, a dead-letter
path, reconciliation) is tracked as `T1-CONNECTOR-RELIABILITY`. Treat everything in this list as a
roadmap, not a guarantee the runtime makes today.

### Why this model

- **The refusal path and the commit path are both in the code.** A reader sees where a handler
  refuses and where it commits, so the question "what happens to the writes when this refuses" is
  answered by reading the handler, not by knowing a framework's hidden rules.
- **The audit records attempts and outcomes, not intentions.** A rolled-back write is on the record
  as rolled back, so a failed write is never mistaken for a success and a success is never missing.
- **The database boundary is honest about the outside world.** Mohio does not pretend it can undo a
  call it cannot undo. By keeping the outside world outside the transaction and saying so, it pushes
  you toward the pattern that actually survives a crash, rather than one that only looks atomic.
