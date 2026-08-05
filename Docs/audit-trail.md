<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
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

## What turns each one on

- A **sector** (`sector: healthcare`, `sector: financial`) audits every write org-wide and
  is what backs a certified compliance claim.
- A **tag** (`[phi]`, `[pci]`, `[pii]`) carries its own audit with or without a sector:
  `[phi]`/`[pci]` reads log DATA_ACCESS, writes touching any tagged field log DATA_CHANGE,
  and permitted `[pii]` uses log PURPOSE_USE.

So even in plain development, with no sector, tagging the data is enough to produce a real
access and usage trail.
