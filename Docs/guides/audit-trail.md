<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# The audit trail

Mohio keeps one audit trail for a program: a durable, tamper-evident log written to the
connected database. Three kinds of event go into it, and they answer the three questions a
compliance review actually asks: what changed, who read protected data, and what personal
data was used and why. Every entry records field names, the actor, and the time. It never
records the values, and never the match values used to find a row.

## The three events

### DATA_CHANGE, every write
Written on `save`, `update`, `upsert`, and `remove`, when the write is under an active
sector or touches a tagged field. Fields:

| Field | Meaning |
|---|---|
| `operation` | `save` / `update` / `remove` / `upsert` |
| `table` | the table written |
| `record_id` | the affected row's id, when known |
| `fields` | the field names written (names only) |
| `session_id`, `member_id` | the actor, when the request carries one |
| `ts`, `audit_id` | timestamp and the entry's own id |

### DATA_ACCESS, reads of protected data
Written on a read (`retrieve`, `find`, `grab`) that actually returned a `[phi]` or `[pci]`
field. HIPAA requires logging every access to health data; PCI DSS requirement 10 requires
the same for cardholder data. The tag carries this on its own, sector or not. Fields:

| Field | Meaning |
|---|---|
| `operation` | `retrieve` / `find` / `grab` |
| `table` | the table read |
| `count` | how many rows the read returned |
| `phi_fields` and/or `pci_fields` | which tagged fields were in the result (names only) |
| `session_id`, `member_id`, `ts`, `audit_id` | actor, time, entry id |

A read that touches both a `[phi]` and a `[pci]` field records both in one entry.

### PURPOSE_USE, allowed uses of personal data
Written when a `[pii]` field is used under a declared purpose that permits it (see
pii-purpose-flow). A use that is blocked is not logged as a use. Fields:

| Field | Meaning |
|---|---|
| `field` | the `[pii]` field used (`(derived)` if the source name was lost) |
| `purpose` | the purpose asserted at the point of use |
| `allowed_purposes` | the purposes the field was collected for |
| `session_id`, `member_id`, `ts`, `audit_id` | actor, time, entry id |

## What is never in the trail

No field values, ever. No match values (the trail says a row in `patients` was read, not
which patient). The trail is safe to retain and to hand to an auditor, because it proves
the handling without itself becoming a second copy of the sensitive data.

## Reading it

At runtime the entries are written to the connected database's audit table. In a running
program you work with the data through your normal queries; in tests the same entries are
available in memory as `interpreter._audit_logs['data_audit_log']`, and you filter by the
`event` field:

```
purpose_uses = [e for e in log if e['event'] == 'PURPOSE_USE']
phi_reads    = [e for e in log if e['event'] == 'DATA_ACCESS' and e.get('phi_fields')]
```

Each entry carries its own `audit_id`, so entries are individually referenceable and the
durable store is tamper-evident: an entry cannot be quietly altered or dropped without the
gap showing.

## Where the trail is written

The trail gets its own connection to the database, separate from the one the program's own
reads and writes use. It has to, because an audit record and a transaction promise opposite
things. A record of what happened must survive whatever happens next, so it commits. A
transaction must be able to undo everything inside it, so it rolls back. On one shared
connection those are the same commit, and the audit's commit was ending the transaction and
keeping a write the program was about to undo. A protected field written inside a failed
transaction stayed in the table, on Postgres, on MySQL and on SQLite alike.

Each record also says what became of the operation it describes, in an `outcome` field:

| outcome | meaning |
|---|---|
| `committed` | the operation stuck |
| `rolled_back` | the transaction it belonged to was undone; the data change is gone |
| `unknown` | the transaction itself failed while ending, so it never said which |

A record for something done inside a transaction is held until the transaction ends, because
until then there is nothing true to write in that field. It is stamped with the time the
ACTION happened, not the time it was written, so holding it does not move it in the trail.

One case has no second connection: an in-memory SQLite database. A second connection to
`:memory:` is not another way into the same store, it is a different, empty one, so the trail
would be written where nothing could read it. An in-memory database is already the one MOHIO™
warns is not for real data.

## Giving the trail its own credential

An audit record is only ever inserted. It is never updated and never deleted. So the audit
connection needs INSERT on the audit tables and nothing else, and the program's own connection
has no business touching those tables at all. Point the audit at its own credential with one
variable:

```bash
export DATABASE_URL=postgresql://app_role:...@host/appdb
export MOHIO_AUDIT_DATABASE_URL=postgresql://audit_writer:...@host/appdb
```

Unset, the audit connects where the application connects, which works and is not the hardened
arrangement. Both may name the same database; what differs is the role.

The roles, set up once by an administrator. The audit tables are created here rather than by
the program, because a role that may only append cannot create anything:

```sql
CREATE ROLE audit_writer LOGIN PASSWORD '...';

-- the audit tables, built once, owned by the administrator
CREATE TABLE phi_audit_log       (...);   -- see the column list below
CREATE TABLE data_audit_log      (...);
CREATE TABLE operation_audit_log (...);
CREATE TABLE fraud_audit_log     (...);
CREATE TABLE compliance_audit    (...);
CREATE TABLE audit_incident_log  (...);

-- the audit connection may add to them and read them back, nothing more
GRANT INSERT, SELECT ON phi_audit_log, data_audit_log, operation_audit_log,
                        fraud_audit_log, compliance_audit, audit_incident_log
   TO audit_writer;

-- the application's own role is granted NOTHING on them
REVOKE ALL ON phi_audit_log, data_audit_log, operation_audit_log,
              fraud_audit_log, compliance_audit, audit_incident_log
  FROM app_role;
```

The columns are the same ones MOHIO™ creates on its own, each `TEXT`, with an `id` primary
key: `audit_id`, `prev_hash`, `entry_hash`, `ts`, `event`, `agent`, `detail`, `decision_name`,
`inputs`, `result`, `confidence`, `model`, `fell_back`, `input_binding`, `sector`,
`session_id`, `member_id`. Any log named `<something>_audit_log` or `<agent>_limits_log` is an
audit table and belongs in the same grant.

What this buys, and it is the database enforcing it rather than the program promising it: the
application's role cannot read, rewrite, scrub or drop the trail, and the audit's role cannot
reach the application's data. A tenant who can run any statement they like through the
application still cannot touch the record of what they did.

Two things to know about the arrangement. The audit's role needs SELECT as well as INSERT,
because each record is chained to the one before it and the writer has to read the end of the
chain. And the unique index that stops two processes claiming the same predecessor is created
on first use, which an append-only role cannot do, so create it alongside the tables
(`CREATE UNIQUE INDEX ux_<log>_prev_hash ON <log> (prev_hash)`) or accept that a fork between
processes is reported by verification rather than prevented by the engine.

## What turns each one on

- A **sector** (`sector: healthcare`, `sector: financial`) audits every write org-wide and
  is what backs a certified compliance claim.
- A **tag** (`[phi]`, `[pci]`, `[pii]`) carries its own audit with or without a sector:
  `[phi]`/`[pci]` reads log DATA_ACCESS, writes touching any tagged field log DATA_CHANGE,
  and permitted `[pii]` uses log PURPOSE_USE.

So even in plain development, with no sector, tagging the data is enough to produce a real
access and usage trail.
