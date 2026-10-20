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

**A protected field needs an `id`-keyed table.** A `[phi]`, `[pci]` or `[pii]` field can only be written to a
table keyed by `id`, because each sealed value is bound to its row's id. A composite-key or other non-`id` table
(a seeded or adopted table can have one) is not supported for protected fields yet: the write refuses as
`protected_field_needs_id_key` (400) and keeps nothing. Composite-key and non-id tables gain protected fields in
6.0.1. To use a protected field now, give the table an `id` primary key (Mohio creates one on every table it
builds).

```mohio
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

## 2a. The egress gate: protected data does not leave the app

A `[phi]`, `[pci]` or `[pii]` value, a value built from one, or a record holding one does not leave
the app unmasked. The rule runs at every boundary, and where it can it runs at CHECK, so a leak is a
build error rather than a production incident. Every example below was run on the current build.

**Page responses mask, and `[pii]` stays plain, as decided.** A `give back` of a record masks `[phi]`
and `[pci]` to the last four characters and leaves `[pii]` in plain text:

```
Response  200  {'id': 1, 'name': 'Ada Lovelace', 'ssn': '****3333', 'pan': '****1111'}
```

`name` is `[pii]` (plain), `ssn` is `[phi]` (`****3333`), `pan` is `[pci]` (`****1111`).

**A field is masked by its OWN table's classification, never by a same-named field of another table.** If
`cards.amount` is `[pci]` and `orders.amount` is untagged, reading `orders.amount` returns the real value: it is
not masked just because a field named `amount` is protected somewhere else. The same holds on every egress path:
the class a read takes, a field in double braces `{{ }}`, a record shown, a lookup list shown, a record given back
(including as JSON), a single field given back, and a file export. A read that names its record unambiguously
(`o.amount`, `rows.first.amount`, `rows.last.amount`, `rows.position.N.amount`) is classified by that record's
table alone. A protected value that is genuinely `[pci]` or `[phi]` stays masked on every path, because the value
carries its class out of decryption. Where a join mixes tables, Mohio keeps every table's classification, so it
can over-mask a nested child but never leak.

**Application logs and diagnostics mask ALL THREE.** `debug.log`, the journey log and `miolog` mask
`[pii]` as well, not only `[phi]` and `[pci]`, because a log is not a page. The same record through
`debug.log`:

```
p = {'id': 1, 'name': '****lace', 'ssn': '****3333', 'pan': '****1111'}
```

Under an active `sector:`, a tagged field in a log is redacted entirely to `[omitted -- <sector>
sector field]`, and a `never log` field to `[omitted -- never log]`.

**AI calls and outbound calls REFUSE at check.** Sending a protected value to an AI model or to the
outside world is refused at build time, with a message that names the fix. An `ai.decide` that weighs
a `[phi]` field:

```
x `weigh p.ssn` in ai.decide is_risky would send [phi] data (`p.ssn`) to an AI model. Protected data does not leave the app (a local model at a loopback address, through ai.connect, is the one AI exception).
  To fix it, mask the value first (`patient.ssn mask.all except last 4`), or leave it out.
```

An outbound `miohttp.post` whose body carries one:

```
x the body of miohttp.post would send [phi] data (`p.ssn`) outside the app. Protected data does not leave the app.
  To fix it, mask the value first (`patient.ssn mask.all except last 4`), or leave it out.
```

**Every outbound boundary refuses a protected value, not only AI and HTTP.** A cookie, a redirect (the `Location`
header), a `mioconnect` call (raw and shaped), `miohttp`, `miomail` and a file write (`miofile`) each refuse a
`[phi]`, `[pci]` or `[pii]` value unless it is masked or the record is sent `stripped`; a shaped connector payload
built with `create` refuses at check too. Cookies, redirects, file writes and the journey log had no protection
before 6.0.0. One gate, `mohio_egress`, decides every one of these, and a boundary test reads every compiler file
and fails the build for any outbound call, file write, cookie, `Location` header, stream write or print that does
not go through the gate or state why it may not carry a value.

To send a useful but safe record to an AI, de-identify it with `stripped` (`weigh p stripped`), which
the gate accepts; for a single scalar, mask it. Both were verified, the `stripped` form checks clean.

**The one AI exception is a local model at a loopback address.** An `ai.connect` chain whose providers
are all declared at a LITERAL loopback address (`localhost`, a `*.localhost` name, or a loopback IP)
is exempt, because the data never leaves the machine. The address must be a literal the compiler can
see at check: an address held in an env var or a secret is not knowable at check and is NOT exempt.
Verified by running, with a passing loopback chain and a refused remote-address control.

## 2b. Secrets reach only their declared use

A secret is referenced as `secret.NAME` and resolves from the environment. It may reach ONLY its
declared use, a connector's credential or the `auth` of an outside call, and nowhere else. Every other
boundary refuses at CHECK, and masking is NOT a way through: a secret is never shown, logged, sent
elsewhere or stored, masked or not. Its declared use works:

```mohio
miohttp.post "https://api.example.com/charge"
    auth secret.API_KEY
    body "amount: 10"
```

(`mio check`: no errors. `miohttp.post` takes no named closer.) Anywhere else refuses at check.
`show secret.API_KEY`:

```
x Line 1: `secret.API_KEY`, or a value made from it, would reach the output of `show`. A secret is used only where it is declared for, a connector's credential or the `auth` of an outside call, and is never shown, logged, sent elsewhere or stored, masked or not.
  To fix it, pass it only as a credential (`auth secret.API_KEY` on the call, or `auth bearer secret.API_KEY` in the connector's block); to show that it is set, check that it exists rather than showing it.
```

Saving one to a stored field refuses the same way, naming the field. The scan covers `show`, `give
back`, render, `miolog`, `debug.log`, cookies, redirects, a connector payload, `miomail`, an AI call's
inputs, `miofile`, the cache, and `grant role`, plus every stored-field write. To prove a secret is
set without revealing it, check that it exists rather than showing it.

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

### Certified profiles and community profiles

A sector profile comes in two kinds, and the tier is decided by what the profile IS, never by
what it says inside it:
- A `.sector` file is a CERTIFIED profile. It must carry a signature that verifies against the
  deployment's trusted keys, or it is REFUSED at load. If a `.sector` exists on the search path it
  is authoritative: it must verify, and the loader NEVER falls back to a same-named `.mho`.
- A `.mho` file is a COMMUNITY profile: free and unsigned. It is used only when no `.sector` of
  the same name exists.

Two settings configure this:
- `MOHIO_SECTOR_TRUSTED_KEYS`: a list of public-key file paths the runtime trusts to verify a
  certified profile's signature.
- `MOHIO_CERTIFIED_SECTORS_DIR`: where certified `.sector` profiles are found (searched first).

Signing a profile is done off the repo, on a trusted machine:
- `python tools/sector_keygen.py --out /secure/path/outside/repo/key` generates a keypair. It
  writes the PRIVATE key to a path OUTSIDE the repository, prints ONLY the public key (safe to
  share and to add to a deployment's trusted set), and REFUSES to write a private key anywhere
  inside the repo tree.
- `python tools/sector_sign.py` signs a `.sector` profile with that private key.

The private key NEVER goes in the repository. That is what makes the certified tier a real
boundary: a profile's claim is only as good as a signature the deployment can verify.

### A profile that names a rule nothing enforces refuses to load

A sector profile can declare a `security requirements` block (for example "mfa required for all staff" or a
failed-login lockout). Until the loader can actually ENFORCE such a rule, a profile that names one REFUSES TO
LOAD, rather than loading and silently not enforcing it (`SectorUnenforceableRuleError`, ruled by Ron
2026-10-04). The reason is the same one the whole compliance floor rests on: a profile that claims a control it
does not deliver is worse than no claim, because a reader trusts the claim. A profile with no `security
requirements` block, or an empty one, is unaffected. This refusal is raised at load, from every entry point that
consults a profile, so a scanner that reads the profile independently reports the same deliberate refusal rather
than a crash.

### An open request contract fails closed under a regulated sector

A request shape with no fields accepts any value a caller sends (see the request-shape guide,
`Docs/guides/form-field-types.md`). While you build, reading a field from a fieldless shape is a
WARNING, because planning a shape and filling it one field at a time is legitimate. Under a
REGULATED sector the same read is an ERROR and `mio check` fails: an unrestricted request contract
is a real exposure, so a regulated app cannot ship one. Verified: the fieldless read that warns
with no sector is refused under `sector: financial`.

### An inbound request must prove who sent it: the three controls

An endpoint that acts on what a caller sends must be able to say the caller is who they claim. Mohio
accepts three controls, and under a regulated sector one of them is required. Outside a sector the
same gap is a warning.

1. **Authentication.** `require role "..."` on the handler. For an endpoint a signed-in user calls.
2. **A signature.** `verify signature using env.SECRET` on the handler. For a webhook, where the
   caller is another service that holds no session but shares a secret. Full syntax and runtime
   behavior are in the manual, `Docs/manual/mohio-manual-part8-ch31-webhook-signatures.md`.
3. **A CSRF-protected browser form.** For a page a person submits in their browser. This one you do
   not declare; a browser form is already protected, so the rule simply does not apply to it.

**Which control fits is inferred from what the handler DOES, not declared by the author.** A handler
that `render`s a page or `jump to`s another address is a browser form, so the rule excludes it. A
handler that acknowledges with a status or JSON (`give back [200] "ok"`) is webhook-shaped, so the
rule flags it if it carries neither a role nor a signature. This is why `/screen` and `/screen-all`,
which render, are not treated as webhooks while `/webhooks/pay`, which acknowledges, is. A webhook
caller holds no role, and a browser form needs no signature, so the control has to match the kind of
endpoint, and the kind is read from its behavior.

Verified under `sector: financial`: a webhook-shaped handler with neither control is refused,

    x `/webhooks/pay` accepts an inbound request in the `financial` sector with nothing proving
      who sent it. Anyone who can reach the address can POST a payload it will act on. Add a
      signature check so only the real sender is accepted:
          verify signature using env.YOUR_WEBHOOK_SECRET
    (Or `require role "..."` if this is a signed-in user's endpoint, not a webhook.)

while the same handler with `require role "service"` or a `verify signature` clause checks clean, and
a rendering handler at `/screen` under the same sector is not flagged at all.

**Secret rotation, under a regulated sector.** A `verify signature` secret must record when it was
rotated. Mohio reads two environment variables named after the secret: `<SECRET>_ROTATED` (the date
it was last rotated) and `<SECRET>_ROTATION` (the interval, for example `90 days`). Under a real
regulated sector both are required, a missing record is a check error, and an expired secret is an
error at check AND at startup, never mid-request, so an app cannot start on a secret past its
interval. Verified messages, on `verify signature using env.WEBHOOK_SECRET`:

    x `/w` uses the secret env.WEBHOOK_SECRET under a regulated sector, so its rotation date must be
      recorded, and env.WEBHOOK_SECRET_ROTATED is not set. ...
    x `/w` uses the secret env.WEBHOOK_SECRET, which is PAST its rotation interval (rotated
      2020-01-01, interval 90 days). Under a regulated sector an app cannot start on an expired
      secret. ...

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
