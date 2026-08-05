<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Compliance in Mohio: the `cm.*` verbs

Mohio treats data-handling compliance as first-class language verbs. The free tier gives
you **the tools**, the primitives you compose into your own regulated logic, with a
built-in audit trail. The **done-for-you** version (the entire regulated process as a
single line, driven by a data shape) is the commercial tier. This guide covers the free
tools.

## The four free verbs

```mohio
connect db as sqlite from env.DATABASE_URL

cm.retain ssn for 6 years        // keep: this data must be kept for the period
cm.expire logs after 30 days     // auto-delete: remove after the period
cm.lock records                  // legal hold: this data must not be deleted
```

`cm.retain`, `cm.expire`, and `cm.lock` **record the policy and write an audit entry**.
They declare your rules and log them; you compose the surrounding logic.

## `cm.purge`: right-to-be-forgotten

`cm.purge` has two forms, and they behave differently on purpose.

### From-form, deletes the matched rows

```mohio
cm.purge from db.members
    match id to 1
    reason "Subject erasure request #4821"
cm.purge: done
```

This **deletes the matched rows from that one table** and writes an audit entry. It is:
- **bounded** — only rows matching the `match` are removed;
- **match-required** — a table-wide purge with no `match` is refused;
- **lock-aware** — if the table is under a `cm.lock` hold, the purge is refused;
- **reason-required** — an erasure without a documented (non-empty) reason is refused.

The `reason` can be a literal or any expression, a variable, `request.field`, or a join,
so you can carry a real ticket reference straight into the audit trail:

```mohio
cm.purge from db.members
    match id to 1
    reason ("Subject erasure #" & request.ticket_id)
cm.purge: done
```

You cascade across related stores by writing one `cm.purge` per table (members, orders,
sessions, ...). That per-table composition is yours to write, it's the regulated logic
the free tools help you express cleanly.

### Value-form, records the request (audit only)

```mohio
cm.purge member.id
    reason "erasure request"
cm.purge: done
```

The value form **records the erasure request and audits it, but does not delete** —
because inferring which table to erase from a bare reference is exactly the kind of
guessing that causes accidental data loss. Name the table with the from-form when you want
deletion; use the value form to log intent.

## The audit trail (two places)

Every `cm.*` call writes to both:
- **`db.compliance_audit`** — a queryable table (auto-created):
  ```mohio
  find history in db.compliance_audit
  find: done
  ```
- **miolog** — a structured `[miolog.audit] compliance ...` line for your log stream.

Each entry carries the action, target/table, duration or reason, and a UTC timestamp.

## Free tools vs. the commercial done-for-you

- **Free (open runtime):** `cm.retain`, `cm.expire`, `cm.lock`, and `cm.purge`, real,
  bounded, audited primitives. You write the regulated process by composing them (one
  `cm.purge` per store to cascade an erasure, your retention rules, your holds).
- **Commercial:** the **entire regulated process as a single line**. You declare a data
  shape that describes where the regulated data lives, and one call runs the whole
  cascade, finds every piece of PII across your stores, erases it, and produces the audit
  artifact. That done-for-you cascade is the paid value; the free tier does not do it for
  you. `cm.report` (managed regulatory filing) and `cm.notify` (managed breach
  notification) are also commercial and fail loud on the open runtime rather than
  silently not-filing.

## Legal note
Mohio activates technical enforcement controls. It does not guarantee compliance.
Qualified legal counsel does.
