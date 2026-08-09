<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# PHI audit-on-access

HIPAA requires an organization to log every **access** to protected health
information, not only every change. Mohio does this for you. When a field is
marked `[phi]`, every read that returns that field writes an entry to the audit
trail automatically, sector or not. You write no logging code.

## Turning it on

Two steps.

**1. Mark the health fields.** Field by field:

```
shape Patient
    name as text
    diagnosis as text [phi]
shape: done
```

Or seal an entire shape as a PHI zone, which marks every field in it:

```
shape Intake [phi]
    ssn as text
    dob as text
    notes as text
shape: done
```

That is all. From here, any `find`, `retrieve`, or `grab` that returns a `[phi]`
field records an access entry, and any write to a `[phi]` field is trailed the
same way. The read itself behaves exactly as before and returns the decrypted
value to authorized code. **You do not need a sector.** The tag carries the
trail on its own; a sector adds the certified compliance claim (HIPAA, SOC2),
org-wide breadth, and retention rules on top.

## What is recorded

Each access entry carries:

- the operation (`find`, `retrieve`, or `grab`)
- the table
- the `[phi]` field names that were returned
- how many rows were read
- who read them (session and member)
- when

It records field **names only, never the values**. A patient's diagnosis text
never enters the audit trail. This is deliberate: an audit log that copied the
data would become a second, unguarded store of the very information it exists to
protect.

The entry lands in the same durable, hash-chained audit trail your data changes
use, so an access record cannot be altered or removed without detection.

## When it does not fire

- **A read that returns no `[phi]` field.** Reading only non-health columns
  records nothing. Untagged reads audit only under an active sector (compliance
  breadth); untagged data on its own is not trailed.

## A note on counts

The row count reflects what was **read from the database**, which is the access
HIPAA cares about. Any in-memory narrowing you apply afterward does not reduce
that count, because the data was already retrieved.

## Querying encrypted fields

A `[phi]` field is encrypted at rest, so you cannot match it by its plaintext
value (`where ssn is "111"` will not find an encrypted `ssn`). Query by a
non-PHI key such as an id or a medical record number, then read the health
fields from the returned record.

---

Mohio activates technical enforcement controls. It does not guarantee
compliance. Qualified legal counsel does.

## PCI cardholder data is access-logged too

The same mechanism covers `[pci]`. PCI DSS requirement 10 requires logging every access to
cardholder data, so a read that returns a `[pci]` field writes the same DATA_ACCESS entry
(with `pci_fields` in place of `phi_fields`), tag-carried, sector or not. A single read
that touches both a `[phi]` and a `[pci]` field logs both classes in one entry.
