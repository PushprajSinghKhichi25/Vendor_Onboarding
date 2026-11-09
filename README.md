# VerifVendor — Autonomous Vendor Onboarding & Verification System

> **Case Study Solution for PS-2: Operations — Vendor Onboarding: From Submission to Approval**

An automated, intelligent operations pipeline that takes real vendor onboarding submissions, executes multi-stage fraud and compliance verification in real time, and outputs an audit-backed decision: **`APPROVED`**, **`PENDING_VENDOR_ACTION`**, or **`REJECTED`**.

For anything not approved, the system autonomously synthesizes an actionable, tailored communication email specifying exactly what discrepancy occurred and how to resolve it, while generating a formal internal SOX-404 procurement audit memorandum.

---

## 1. System Architecture & The 5 Verification Stages

```
   [ Vendor Submission Payload ]
                 │
                 ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 1: Intake & Document Completeness Check          │
│ • Validates mandatory entity, tax, & banking fields    │
│ • Inspects Certificate of Inc, W-9/VAT, Bank Proof     │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 2: Tax Registration & Jurisdictional Syntax      │
│ • Validates country-specific syntax (US EIN, UK/DE/FR) │
│ • Validates IRS campus code prefixes (detects 00-, etc)│
│ • Flags cross-border tax jurisdiction divergences      │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 3: Bank Cross-Reference & Entity Matching        │
│ • Fuzzy & token-sort matching (Legal Name vs Bank Beneficiary)
│ • Identifies benign DBAs & corporate subsidiaries      │
│ • Screens routing countries for offshore secrecy havens│
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 4: Risk, AML & Fraud Screening                   │
│ • Screens OFAC / EU sanctions & PEP watchlists         │
│ • BEC / Account Takeover: checks for duplicate IBANs   │
│ • Shell velocity: flags entities incorporated < 60 days │
│ • Dispatches disposable / free webmail domain flags    │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 5: Autonomous Decision & Action Synthesizer      │
│ • Calculates composite Risk Score (0–100) & Risk Tier  │
│ • Assigns Status: APPROVED / PENDING / REJECTED        │
│ • Drafts tailored Vendor Communication Email           │
│ • Generates SOX-404 Compliance Audit Memo              │
│ • Writes immutable record to SQLite Audit Ledger       │
└────────────────────────────────────────────────────────┘
```

---

## 2. The 4 Deliberate, Non-Trivial Edge Cases

In vendor operations, simple regex checks fail because real companies have complex structures, subsidiaries, and malicious actors targeting AP departments. We designed 4 realistic edge cases:

| Scenario | Type | Observed Anomaly | System Behavior | Final Status |
| :--- | :--- | :--- | :--- | :--- |
| **Happy Path** | Standard | All fields valid, matching bank, clean OFAC | Direct automated approval | **`APPROVED`** (Risk: 0/100) |
| **Edge Case 1** | Benign DBA Mismatch | Legal name is *Nordic Industrial Solutions GmbH*, but bank account is named *Nordic Logistics Services Ltd* (operating subsidiary) | Token similarity is ~65%. Rather than hard-rejecting, system flags a DBA/subsidiary relationship and drafts an email requesting a DBA certificate or parent linking resolution. | **`PENDING_VENDOR_ACTION`** (Risk: 25/100) |
| **Edge Case 2** | Offshore Secrecy & Shell Velocity | UK registered entity routes bank disbursement to an offshore haven (Seychelles - `SC`), with entity age < 20 days and generic `@mailinator.com` email | Capital flight / AML risk trigger. Company country != Bank country (financial secrecy jurisdiction) + shell company velocity. Blocked for executive review. | **`REJECTED`** (Risk: 100/100) |
| **Edge Case 3** | Tax Syntax Inconsistency | Claims US incorporation but provides an invalid 7-digit Tax ID with an unissued `00-` prefix + missing bank proof doc | Syntax and IRS campus code validation catches jurisdiction format violation. Auto-generates vendor request quoting IRS CP-575 / W-9 requirements. | **`PENDING_VENDOR_ACTION`** (Risk: 50/100) |
| **Edge Case 4** | Duplicate Bank Hijacking (BEC Attack) | Attacker submits invoice payment details using an IBAN already registered in the ERP to *Global Supplies Inc* | Historical ERP cross-reference catches duplicate bank coordinates. Flags critical Vendor Email Compromise / Payment Diversion alert and freezes onboarding. | **`REJECTED`** (Risk: 60/100) |

---

## 3. How to Run the Live System

The system is built entirely using Python 3 standard library (`http.server`, `sqlite3`, `json`, `re`, `urllib`). **Zero external `pip` dependencies required.**

### Quick Start:

```bash
# 1. From workspace root, start the application:
./start_vendor_system.sh

# 2. Or directly via Python:
python3 vendor_onboarding_system/server.py
```

### Accessing the System:
- **Interactive UI**: [http://localhost:8080](http://localhost:8080)
- **Runs Audit API**: [http://localhost:8080/api/runs](http://localhost:8080/api/runs)
- **KPI Metrics API**: [http://localhost:8080/api/stats](http://localhost:8080/api/stats)
- **Export Audit CSV**: [http://localhost:8080/api/export](http://localhost:8080/api/export)

### Running CLI Automated Tests:
```bash
python3 vendor_onboarding_system/test_cli.py
```

---

