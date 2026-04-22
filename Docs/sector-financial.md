# Mohio Sector Profile — Financial

**Declaration:** `sector: financial`  
**Activates:** PCI_DSS v4, SOC2  
**Jurisdiction:** US federal — PCI-DSS v4, BSA/AML, FinCEN  
**Version:** 1.0 | Last reviewed: 2026-03-24

---

## One declaration. Everything activates.

```mohio
sector: financial
```

That single line gives the Mohio compiler and runtime complete knowledge of the financial domain — field types, compliance frameworks, regulatory thresholds, access control defaults, AI decision constraints, audit requirements, and security minimums.

No library installation. No configuration files. No manual wiring.

**Your declaration wins.** Any rule in this profile can be overridden by declaring it explicitly in your file after the `sector:` line.

---

## What activates automatically

### Compliance frameworks

- **PCI_DSS v4** — Payment Card Industry Data Security Standard
- **SOC2** — Service Organization Control 2

### Data retention (BSA/AML federal minimums)

| Data type | Retention |
|-----------|-----------|
| Financial records | 5 years |
| Transactions | 5 years |
| Audit logs | 5 years |
| KYC records | 5 years |
| PII | 3 years |
| Card data | Never retained — tokenize immediately |
| Sessions | Expire after 15 minutes |
| Auth tokens | Expire after 24 hours |

### Field type awareness

The language automatically recognizes these field names and applies the correct classification — no manual tagging required.

| Field name | Classification | Notes |
|------------|---------------|-------|
| `account_number` | [pci, pii] | Bank account number |
| `routing_number` | [pci] | ABA routing number |
| `card_number` | [pci] | Payment card number |
| `card_cvv` | [pci] | **Never stored — compiler enforces** |
| `card_expiry` | [pci] | MM/YY format |
| `card_holder` | [pci, pii] | Cardholder name |
| `card_token` | [identifier] | Safe payment token |
| `ssn` | [pii] | Social Security Number |
| `ein` | [identifier] | Employer Identification Number |
| `itin` | [pii] | Individual Taxpayer ID |
| `transaction_id` | [financial_record, identifier] | |
| `transaction_amount` | [financial_record] | |
| `transaction_date` | [financial_record] | |
| `merchant_id` | [identifier] | |
| `merchant_name` | [financial_record] | |
| `merchant_category` | [financial_record] | MCC code |
| `wire_reference` | [financial_record] | |
| `swift_code` | [identifier] | SWIFT/BIC |
| `iban` | [pci] | International bank account |
| `balance` | [financial_record] | |
| `credit_score` | [pii] | Range 300–850 |
| `risk_score` | [financial_record] | |
| `kyc_status` | [kyc_record] | Know Your Customer status |
| `aml_flag` | [financial_record] | AML flag |
| `ctr_amount` | [financial_record] | CTR threshold: $10,000 |
| `sar_reference` | [financial_record] | SAR reference |

### Regulatory thresholds (built in)

| Threshold | Value | Regulation |
|-----------|-------|-----------|
| CTR threshold | $10,000 | BSA — Currency Transaction Report required |
| Structuring watch | $9,000 | Watch for structuring below CTR threshold |
| SAR threshold | $5,000 | FinCEN — Suspicious Activity Report |
| Wire scrutiny | $3,000 | Travel Rule — documentation required |
| OFAC check | All transactions | Required on all transactions |

### Access control defaults

| Access | Requires |
|--------|---------|
| Any [pci] field | Authentication |
| Any [financial_record] field | Authentication |
| Any route touching [pci] | Role: `payment_processor` or `admin` or `system` |
| Any route touching `card_number` | Role: `payment_processor` or `system` |
| Any route touching [kyc_record] | Role: `compliance` or `admin` |
| Any route touching `aml_flag` | Role: `compliance` or `admin` or `fraud_analyst` |

### PCI-DSS specific rules (compile-time enforced)

- `card_number` — never logged
- `card_cvv` — never stored, never logged
- `card_expiry` — never logged in full
- `card_number` — must tokenize before storage
- All [pci] fields — encrypted at rest using AES-256
- All [pci] fields — encrypted in transit using TLS 1.2 or higher

### AI decision constraints

| Decision type | Minimum confidence | Additional requirements |
|--------------|-------------------|------------------------|
| Transaction approval or denial | 0.85 | `ai.audit to transaction_audit_log`, `ai.explain` to fraud analyst |
| Credit approval | 0.90 | Human review required if denial; `ai.explain` to applicant (ECOA) |
| SAR filing trigger | 0.95 | Human review required before filing |
| Account closure | 0.95 | Human review required before action |

### Audit requirements

| Event | Log |
|-------|-----|
| All reads of [financial_record] | `financial_audit_log` |
| All writes of [pci] fields | `pci_audit_log` |
| All failed auth attempts | `security_audit_log` |
| All admin actions | `admin_audit_log` |
| All ai.decide outcomes | `ai_decision_log` |

### Breach notification defaults

- Affected individuals: within 30 days
- Relevant card brands: within 24 hours (if [pci] involved)
- Acquiring bank: within 24 hours (if [pci] involved)
- FinCEN: via SAR process if required

### Security minimums

- All [pci] fields encrypted at rest — AES-256
- All connections encrypted — TLS 1.2 or higher
- Session timeout: 15 minutes (payment flows), 30 minutes (general banking)
- MFA required for all administrative roles
- MFA required for wire transfer initiation
- API keys rotate every 90 days
- Annual penetration testing required

### Automatic regulatory reporting

When a transaction exceeds $10,000 in cash, the profile automatically triggers CTR reporting. When a transaction sets an AML flag, a compliance alert fires through the secure channel defined in `env.COMPLIANCE_CHANNEL`.

---

## What this profile does not cover

Transparency about limitations is a trust feature. These items require additional action outside Mohio:

- **State money transmission licenses** — vary significantly by state; consult legal counsel
- **FINRA rules** — apply to broker-dealer operations; add `compliance: FINRA` separately
- **Investment advisor rules (SEC/RIA)** — not covered; add `compliance: RIA` separately
- **Cryptocurrency operations** — have additional FinCEN requirements
- **FCRA** — credit reporting requirements; add `compliance: FCRA` if applicable
- **ECOA adverse action notices** — `ai.explain` handles the explanation, but the legal process is an obligation outside code
- **BSA independent testing** — an audit obligation, not a code concern

A Mohio program that declares `sector: financial` activates technical enforcement controls. It does not guarantee compliance. Qualified legal counsel and independent audits remain obligations of the organization.

---

## Example

```mohio
sector: financial

connect db as postgres from env.DATABASE_URL

ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh transaction.amount, transaction.device_id, member.history, recent
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
ai.decide: done
```

`sector: financial` activates everything above. The `ai.decide` block inherits the 0.85 minimum confidence constraint for transaction decisions, the audit requirement, and the ECOA explanation requirement — without a single additional line of configuration.

---

*Mohio Sector Profile: Financial — v1.0 — Particular LLC — Apache 2.0*  
*Maintained by the Mohio Language Project. Updated as regulations change.*
