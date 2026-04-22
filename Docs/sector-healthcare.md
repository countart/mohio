# Mohio Sector Profile — Healthcare

**Declaration:** `sector: healthcare`  
**Activates:** HIPAA, HITECH  
**Jurisdiction:** US federal — HIPAA/HITECH  
**Version:** 1.1 | Last reviewed: 2026-03-25  
**Changelog:** v1.1 — MFA elevated from recommended to required per HIPAA Security Rule update (Jan 2025). Encryption at rest and in transit now mandatory with no addressable exceptions.

---

## One declaration. Everything activates.

```mohio
sector: healthcare
```

That single line gives the Mohio compiler and runtime complete knowledge of the healthcare domain — PHI field types, HIPAA and HITECH compliance frameworks, AI decision constraints for clinical settings, 6-year retention enforcement, audit logging on all PHI access, and breach notification defaults.

No library installation. No configuration files. No manual wiring.

**Your declaration wins.** Any rule in this profile can be overridden by declaring it explicitly in your file after the `sector:` line.

---

## What activates automatically

### Compliance frameworks

- **HIPAA** — Health Insurance Portability and Accountability Act
- **HITECH** — Health Information Technology for Economic and Clinical Health Act

### Data retention (federal minimums)

| Data type | Retention |
|-----------|-----------|
| PHI (Protected Health Information) | 6 years |
| PII | 3 years |
| Audit logs | 6 years |
| Identifiers | 6 years |
| Sessions | Expire after 8 hours |

### PHI field type awareness

The language automatically recognizes these field names as PHI — no manual tagging required. Access controls and audit logging activate automatically.

| Field name | Classification | Label |
|------------|---------------|-------|
| `mrn` | [phi] | Medical Record Number |
| `npi` | [identifier] | National Provider Identifier |
| `dea` | [identifier] | DEA Number |
| `diagnosis` | [phi] | Diagnosis / ICD Code |
| `diagnosis_code` | [phi] | ICD-10 Diagnosis Code |
| `dob` | [phi, pii] | Date of Birth |
| `ssn` | [phi, pii] | Social Security Number |
| `insurance_id` | [phi] | Insurance Member ID |
| `insurance_group` | [phi] | Insurance Group Number |
| `prescription` | [phi] | Prescription Data |
| `medication` | [phi] | Medication Name / Dosage |
| `lab_result` | [phi] | Laboratory Result |
| `vital_sign` | [phi] | Vital Sign Reading |
| `clinical_note` | [phi] | Clinical Note / Narrative |
| `treatment_plan` | [phi] | Treatment Plan |
| `referral` | [phi] | Referral Record |
| `allergy` | [phi] | Allergy Record |
| `immunization` | [phi] | Immunization Record |
| `patient_name` | [phi, pii] | Patient Full Name |
| `patient_email` | [pii] | Patient Email Address |
| `patient_phone` | [pii] | Patient Phone Number |
| `patient_address` | [phi, pii] | Patient Address |
| `provider_name` | [identifier] | Provider Name |
| `facility_id` | [identifier] | Facility Identifier |
| `encounter_id` | [phi] | Encounter / Visit ID |
| `procedure_code` | [phi] | CPT Procedure Code |
| `billing_code` | [phi] | Billing Code (ICD-10-PCS) |

### Access control defaults

| Access | Requires |
|--------|---------|
| Any [phi] field | Authentication |
| Any [pii] field | Authentication |
| Any route touching [phi] | Role: `clinician` or `admin` or `system` |
| Any route touching [pii] | Role: `staff` or `clinician` or `admin` or `system` |

### AI decision constraints

Clinical AI decisions carry the highest confidence thresholds in any Mohio sector profile — patient safety is the reason.

| Decision type | Minimum confidence | Additional requirements |
|--------------|-------------------|------------------------|
| Diagnosis or treatment | 0.95 | Human review required before action; `ai.explain` to clinician |
| Medication or prescription | 0.98 | Human review required before action — highest threshold |
| Billing or procedure codes | 0.90 | `ai.audit to billing_audit_log` |

The 0.98 threshold on medication decisions is intentional and not overridable without explicit justification. Patient safety requires it.

### Audit requirements

All PHI access is logged automatically — reads, writes, and deletes.

| Event | Log | Notes |
|-------|-----|-------|
| All reads of [phi] fields | `phi_audit_log` | Automatic |
| All writes of [phi] fields | `phi_audit_log` | Automatic |
| All deletes of [phi] fields | `phi_audit_log` | Reason required |
| All failed auth attempts | `security_audit_log` | Automatic |

### Breach notification defaults

When a breach is detected and `cm.notify breach` is called:

- HHS notification: `https://ocrportal.hhs.gov`
- Affected individuals: within 60 days
- Media notification: required if more than 500 individuals affected in a single state
- Required content: nature of breach, PHI types involved, estimated affected count, mitigation steps, contact information

### Security minimums (v1.1 — all mandatory)

Per the January 2025 HIPAA Security Rule update, the previous "addressable" vs "required" distinction has been eliminated. All specifications below are now mandatory with no exceptions.

| Requirement | Standard |
|-------------|---------|
| PHI at rest | AES-256 encryption — **mandatory** |
| PHI in transit | TLS 1.2 or higher — **mandatory** |
| PII at rest | AES-256 encryption |
| PII in transit | TLS 1.2 or higher |
| Session timeout | Maximum 8 hours |
| Failed login lockout | After 10 attempts |
| Password minimum length | 8 characters |
| MFA | Required for all users — **mandatory as of Jan 2025** |
| MFA | Required for all clinical roles — no exceptions |
| MFA | Required for all administrative access to [phi] data |
| Annual compliance audit | **Mandatory under updated rule** |

---

## What this profile does not cover

Transparency about limitations is a trust feature. These items require additional action outside Mohio:

- **State-specific retention laws** — may exceed federal minimums; check your state
- **HIPAA Business Associate Agreements (BAAs)** — a legal obligation, not a code concern
- **42 CFR Part 2** — substance use records have stricter rules; add `compliance: CFR42` separately
- **Mental health records** — may have additional state protections
- **Pediatric records** — minors under 18 may require parental consent handling; add `compliance: COPPA` if applicable
- **Annual compliance audits** — now mandatory under the updated rule; plan accordingly
- **EU health data** — GDPR and EU medical data regulations require additional declarations

A Mohio program that declares `sector: healthcare` activates technical enforcement controls. It does not guarantee HIPAA compliance. Qualified legal counsel, a signed BAA with Anthropic if using the AI runtime, and independent audits remain obligations of the organization.

Run `mio check sector healthcare` to verify this profile is current before deployment.

---

## Example

```mohio
sector: healthcare

connect db as postgres from env.DATABASE_URL

listen for

    new sh.ClinicalDecision
        require role "clinician" or "system"

        retrieve patient from db.patients
            match mrn is request.mrn
        retrieve: done

        ai.decide recommendTreatment(patient) returns text
            check confidence above 0.95
            weigh patient.diagnosis, patient.medication, patient.allergy, patient.vital_sign
            ai.audit to phi_audit_log
            not confident
                give back 202 "Referred to attending physician"
        ai.decide: done

        give back 200 recommendTreatment

    new: done

listen: done
```

`sector: healthcare` activates everything above. The `ai.decide` block inherits the 0.95 minimum confidence requirement for clinical decisions, the mandatory human review requirement, the PHI audit log, and the `ai.explain` requirement for the clinician — without a single additional line.

---

*Mohio Sector Profile: Healthcare — v1.1 — Particular LLC — Apache 2.0*  
*Maintained by the Mohio Language Project. Updated as regulations change.*  
*v1.1: MFA and encryption now mandatory per Jan 2025 HIPAA Security Rule update.*
