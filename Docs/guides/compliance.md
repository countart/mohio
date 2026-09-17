<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Compliance

Most languages treat compliance as something you bolt on: libraries, middleware, review
checklists. Mohio treats it as part of the language. You tag the data for what it is, and
the compiler enforces the handling, encryption, masking, purpose limits, and audit trails,
so whole classes of mistake become things you cannot express by accident.

This chapter is the map. Each control has its own page with the full detail; this ties
them together and shows how the free tag layer and the paid sector layer relate.

---

## 1. Tag the data for what it is

Three inline tags mark sensitive data. They are free, part of the open-core language, and
each one turns on the handling that data legally needs.

| Tag | Data | What the tag does |
|---|---|---|
| `[phi]` | Protected health information (HIPAA) | Encrypted at rest. Every **read** is logged (audit-on-access). |
| `[pci]` | Cardholder data (PCI DSS) | Encrypted at rest. Masked to last-4 on any display, mask follows the value through concatenation, and every read is logged (req 10). |
| `[pii]` | Personal data (GDPR) | Encrypted at rest. Carries the purpose it was collected for; using it for another purpose fails loud. |

```
shape Patient
    name as text
    mrn as text
    diagnosis as text [phi]
shape: done

shape Card
    number as text [pci]
shape: done

shape Customer
    email as text [pii] purpose "account"
    ad_id as text [pii] purpose "marketing"
shape: done
```

You never write the encryption, the masking, the access log, or the purpose check. The
tag does it. Full detail: **phi-audit-on-access**, **pci-masking**, **pii-purpose-flow**.

---

## 2. The three controls in one breath

**`[phi]` -> audit on access.** Reading a `[phi]` field writes an entry to the audit
trail, field names only, never values. HIPAA requires logging every *access* to health
data, not just every change, so reads are logged the same way writes are.

**`[pci]` -> mask that follows the value, plus access logging.** `show card.number` prints
`****1234`. Weave it into a string and it is still masked: `show ("Card: " & card.number)`
prints `****1234`, so a full card number cannot leak by concatenation. The number stays
full at rest and for internal use; to use it, pass the raw value to a processor, never a
built display string. And, per PCI DSS req 10, every read of a `[pci]` field is logged to
the audit trail, the same way `[phi]` reads are.

**`[pii]` -> purpose-bound flow.** A `[pii]` field remembers the purpose it was collected
for. Assert a purpose and any use of the field under a different one fails loud:

```
purpose "marketing"
    miomail.send to customer.ad_id ...    // ok: ad_id is for marketing
    show customer.email                   // FAILS: email is for account
purpose: done
```

The purpose follows the value even when you copy it (`hold e customer.email`) or build a
string from it, and a value made from several `[pii]` fields must satisfy the purpose of
every one. For a single line, use the shorthand `show customer.email for.purpose "account"`.

---

## 3. The audit trail

Reads of `[phi]` or `[pci]`, writes that touch a sensitive field, and every allowed `[pii]` use are
written to one durable, tamper-evident (hash-chained) trail in the connected database. It
records the operation or field, the purpose (for a PII use), who did it, and when, never
the value, never the match value. A blocked use is not logged as a use.

The result is a straight answer to the two questions an auditor asks: who accessed this
data, and every time this data was used, for what declared purpose.

---

## 4. The sector layer

The tags work on their own. A **sector** raises the ceiling:

```
sector: healthcare
```

A sector adds org-wide breadth (every write is audited, not only tagged ones), retention
rules, AI confidence floors, and, on the certified tier, the compliance *claim*. Non-
conforming code does not compile. A sector never lowers a protection a tag already gives;
it only adds.

The division is deliberate: the **tag is the floor** (free, always on), the **sector is
the ceiling** (breadth, retention, and the certified claim).

---

## 5. Turning a control off: `security: off`

Sometimes you are prototyping and a control is in your way before you have real data.
Turning it off is possible, but never silent. It is an explicit, recorded posture with a
reason and an expiry:

```
security: off
    reason "prototype, no real PHI yet"
    expires "2026-08-01"
```

This is an honest security-debt marker, not a hidden bypass: the reason and the expiry are
recorded, and it cannot be used to disable a control that an active sector mandates. If you
have to write it down and date it, you will remember to take it back out.

---

## 6. What we claim, and what we do not

The enforcement is real, logged, and auditable. It is still a *mechanism*, not a legal
guarantee. The honest line, which appears on every compliance statement, word for word:

> Mohio activates technical enforcement controls. It does not guarantee compliance.
> Qualified legal counsel does.

Free and community profiles are described as "sector-base enforced at the compiler level"
and "code-compliant to the declared profile." Only the certified tier uses "certified code
compliance," and even there a framework name is always qualified with "code" (for example,
"HIPAA **code** compliant"). Mohio never claims that an organization is "HIPAA compliant"
or "SOC 2 compliant" at any tier, because that is an organizational fact, not a code fact.
