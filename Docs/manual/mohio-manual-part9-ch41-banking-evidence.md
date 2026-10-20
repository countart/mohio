<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 41: Banking evidence, built today versus planned

This chapter draws the line between what the runtime guarantees for a banking app today and what is planned, each
named with its backlog item, so nothing reads as a claim beyond what runs.

**Built today:**

- A hash-chained, append-only AUDIT TRAIL in your own database, verifiable with `mio audit verify`, with fork
  detection through `verify-anchors` (Chapter 39).
- The DURABLE PERSON at the authentication boundary: `grant role for person` sets `session.person`, read-only and
  set once, stamped automatically onto every audit record, with maker-checker separation (Chapter 29).
- `require role` access control, and the SECTOR FLOOR that refuses a regulated write to storage that cannot keep
  the promise (see `Docs/guides/databases-supported.md`).
- Exact money arithmetic and currency precision (see the money chapter), and the rounding policy
  (`Docs/design/DECISION-rounding-policy-2026-10-05.md`).

**Planned, each named:**

- A real BANKING SECTOR PROFILE carrying enforceable lending controls (no disbursement before due diligence, a
  verified authorised signatory, beneficial-owner verification, PEP approval): `T1-SECTOR-BANKING`, with a Bahrain
  layer `T1-SECTOR-BAHRAIN`. These need the profile-rules mechanism `T1-PROFILE-RULES`.
- EXTERNAL AUDIT ANCHORING, publishing chain heads so the trail cannot be truncated or replaced undetected:
  `T1-AUDIT-ANCHORING` (the verify side already ships; the publish side is the build).
- A BANK-HELD MIRROR of the trail, a copy the bank holds independently of the app's database:
  `T1-BANK-HELD-MIRROR`.
- Identity verification at onboarding through a licensed vendor or national ID: `T1-IDENTITY-VERIFICATION-VENDOR`.
- Immediate SESSION REVOCATION when an account is disabled or a role removed: `T1-SESSION-REVOCATION`.

Until a planned item ships, do not present its guarantee as in force. The audit trail, the durable person, access
control and the sector floor are what a banking app can rely on from the runtime today.
