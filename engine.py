"""
Intelligent Verification & Decision Engine for Vendor Onboarding.

Executes a 5-stage automated assessment:
  Stage 1: Intake & Document Completeness
  Stage 2: Tax Registration & Jurisdictional Syntax
  Stage 3: Bank Account Cross-Reference & Entity Matching
  Stage 4: Risk, AML & Fraud Screening (Sanctions, Shell Indicators, BEC Bank Hijack)
  Stage 5: Autonomous Decision & Action Synthesizer (Score, Status, Vendor Email, AP Audit Memo)
"""

import difflib
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .db import find_conflicting_vendor_bank, search_sanctions_watchlist
except (ImportError, ValueError):
    from db import find_conflicting_vendor_bank, search_sanctions_watchlist


# Recognized secrecy jurisdictions & high-risk offshore tax havens
OFFSHORE_HAVENS = {
    "SC": "Seychelles",
    "KY": "Cayman Islands",
    "CY": "Cyprus",
    "PA": "Panama",
    "BZ": "Belize",
    "VG": "British Virgin Islands",
    "BS": "Bahamas",
    "VU": "Vanuatu",
    "MH": "Marshall Islands",
}

# Standard corporate suffixes for entity normalization
CORPORATE_SUFFIXES = [
    r"\binc(?:\.|\b)",
    r"\bcorp(?:\.|\b)",
    r"\bcorporation\b",
    r"\bllc(?:\.|\b)",
    r"\bltd(?:\.|\b)",
    r"\blimited\b",
    r"\bgmbh\b",
    r"\bco(?:\.|\b)",
    r"\bcompany\b",
    r"\bs\.a\.(?:\.|\b)",
    r"\bsa\b",
    r"\bs\.e\.(?:\.|\b)",
    r"\bse\b",
    r"\bplc\b",
]

# Disposable / free webmail domains for corporate legitimacy screening
GENERIC_MAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "mailinator.com",
    "protonmail.com",
    "proton.me",
    "aol.com",
    "tempmail.com",
}


def normalize_entity_name(name: str) -> str:
    """Normalize company name by stripping legal punctuation, suffixes, and whitespace."""
    if not name:
        return ""
    clean = name.lower()
    for suffix in CORPORATE_SUFFIXES:
        clean = re.sub(suffix, "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[^\w\s]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def calculate_name_similarity(name1: str, name2: str) -> float:
    """Compute string similarity ratio between two company names using token and sequence matching."""
    norm1 = normalize_entity_name(name1)
    norm2 = normalize_entity_name(name2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0

    # Sequence matcher
    seq_ratio = difflib.SequenceMatcher(None, norm1, norm2).ratio()

    # Token overlap (Jaccard on words)
    tokens1 = [w for w in norm1.split() if len(w) > 2]
    tokens2 = [w for w in norm2.split() if len(w) > 2]
    token_overlap = 0.0
    if tokens1 and tokens2:
        set1, set2 = set(tokens1), set(tokens2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        token_overlap = intersection / union
        # If the primary brand name (first non-generic word) is identical, bonus weight
        if tokens1[0] == tokens2[0]:
            token_overlap = max(token_overlap, 0.65)

    return round(max(seq_ratio, token_overlap, (seq_ratio + token_overlap) / 2), 3)


def validate_tax_id_syntax(country: str, tax_id: str) -> Tuple[bool, str]:
    """
    Validate tax identification numbers against country-specific syntax and jurisdiction rules.
    Returns: (is_valid, reason)
    """
    country = country.strip().upper()
    tid = tax_id.strip().replace(" ", "").replace("-", "")

    if country == "US":
        # US EIN: 9 digits. Format XX-XXXXXXX.
        raw = tax_id.strip()
        ein_pattern = r"^\d{2}-\d{7}$"
        if not re.match(ein_pattern, raw) and not (len(tid) == 9 and tid.isdigit()):
            return False, f"US EIN must be 9 digits (format XX-XXXXXXX). Found '{raw}'."

        # Check for invalid IRS EIN campus prefixes (IRS never issues 00-, 07-, 08-, 09-, 17-, 18-, 19-, etc.)
        prefix = tid[:2]
        invalid_prefixes = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70", "78", "79", "89", "96", "97"}
        if prefix in invalid_prefixes:
            return False, f"US EIN prefix '{prefix}' is not an active IRS issuance campus code."
        return True, "Valid US EIN format"

    elif country == "GB" or country == "UK":
        # UK VAT: 9 numeric digits, or prefixed with GB
        clean = tax_id.strip().upper().replace(" ", "")
        if clean.startswith("GB"):
            clean = clean[2:]
        if len(clean) == 9 and clean.isdigit():
            return True, "Valid UK VAT format"
        return False, f"UK VAT requires 9 numeric digits (e.g. GB123456789). Found '{tax_id}'."

    elif country == "DE":
        # German VAT: DE + 9 digits
        clean = tax_id.strip().upper().replace(" ", "")
        if re.match(r"^DE\d{9}$", clean):
            return True, "Valid German VAT (USt-IdNr)"
        return False, f"German VAT ID must start with 'DE' followed by 9 digits. Found '{tax_id}'."

    elif country == "FR":
        # French VAT: FR + 2 alphanumeric + 9 digits
        clean = tax_id.strip().upper().replace(" ", "")
        if re.match(r"^FR[A-Z0-9]{2}\d{9}$", clean):
            return True, "Valid French VAT format"
        return False, f"French VAT ID must start with 'FR' followed by 11 characters. Found '{tax_id}'."

    elif country == "CA":
        # Canada Business Number: 9 digits
        if len(tid) == 9 and tid.isdigit():
            return True, "Valid Canadian Business Number (BN9)"
        return False, f"Canadian Business Number must be 9 numeric digits. Found '{tax_id}'."

    elif country == "AU":
        # Australian Business Number (ABN): 11 digits
        if len(tid) == 11 and tid.isdigit():
            return True, "Valid Australian Business Number (ABN)"
        return False, f"Australian Business Number (ABN) must be 11 numeric digits. Found '{tax_id}'."

    # General fallback
    if len(tid) >= 6:
        return True, "Tax ID format accepted under standard international rules"
    return False, f"Tax ID '{tax_id}' is too short or malformed for country {country}."


def verify_vendor_submission(submission: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the full 5-stage automated verification process on a vendor onboarding submission.
    """
    start_time = time.time()
    stages = []
    discrepancies = []
    risk_score = 0  # 0 to 100

    # Extract inputs with defaults
    company_info = submission.get("company_info", {})
    bank_info = submission.get("banking_info", {})
    tax_info = submission.get("tax_info", {})
    docs = submission.get("documents", {})

    legal_name = company_info.get("legal_name", "").strip()
    dba_name = company_info.get("dba_name", "").strip()
    company_country = company_info.get("country", "").strip().upper()
    inc_date_str = company_info.get("incorporation_date", "").strip()
    contact_email = company_info.get("contact_email", "").strip()
    address = company_info.get("address", "").strip()

    tax_id = tax_info.get("tax_id", "").strip()
    tax_country = tax_info.get("tax_country", company_country).strip().upper()

    bank_name = bank_info.get("bank_name", "").strip()
    account_holder = bank_info.get("account_holder_name", "").strip()
    account_number = bank_info.get("account_number", "").strip()
    iban = bank_info.get("iban", "").strip().replace(" ", "").upper()
    swift_bic = bank_info.get("swift_bic", "").strip()
    bank_country = bank_info.get("bank_country", company_country).strip().upper()

    # ══════════════════════════════════════════════════════════════
    # STAGE 1: Intake & Document Completeness
    # ══════════════════════════════════════════════════════════════
    s1_t0 = time.time()
    s1_missing = []

    if not legal_name:
        s1_missing.append("Company Legal Name")
    if not company_country:
        s1_missing.append("Country of Incorporation")
    if not tax_id:
        s1_missing.append("Tax Identification Number")
    if not bank_name:
        s1_missing.append("Bank Name")
    if not account_holder:
        s1_missing.append("Bank Account Holder Name")
    if not account_number and not iban:
        s1_missing.append("Bank Account Number or IBAN")
    if not contact_email:
        s1_missing.append("Authorized Contact Email")

    # Document attachments verification
    has_cert_inc = docs.get("certificate_of_incorporation", False)
    has_tax_doc = docs.get("tax_document", False)  # W9 or VAT cert
    has_bank_proof = docs.get("bank_statement_or_voided_check", False)

    missing_docs = []
    if not has_cert_inc:
        missing_docs.append("Certificate of Incorporation / Business License")
    if not has_tax_doc:
        missing_docs.append("Tax Compliance Document (Form W-9 / W-8BEN-E or VAT Registration)")
    if not has_bank_proof:
        missing_docs.append("Proof of Banking (Bank Confirmation Letter or Voided Check)")

    s1_status = "PASSED"
    s1_message = "All core mandatory fields and required compliance documents present."

    if s1_missing:
        s1_status = "FAILED"
        risk_score += 40
        msg = f"Missing required registration fields: {', '.join(s1_missing)}."
        discrepancies.append({
            "stage": "Intake & Completeness",
            "severity": "CRITICAL",
            "field": "registration_fields",
            "issue": msg,
            "remediation": "Provide all mandatory fields in the vendor registration portal.",
        })
        s1_message = msg

    if missing_docs:
        risk_score += 15
        doc_msg = f"Missing compliance attachments: {', '.join(missing_docs)}."
        discrepancies.append({
            "stage": "Intake & Completeness",
            "severity": "MEDIUM",
            "field": "documents",
            "issue": doc_msg,
            "remediation": f"Upload verified PDF copies of: {', '.join(missing_docs)}.",
        })
        if s1_status == "PASSED":
            s1_status = "WARNING"
            s1_message = doc_msg

    stages.append({
        "id": "stage_1_intake",
        "name": "Intake & Document Completeness",
        "icon": "📋",
        "status": s1_status,
        "duration_ms": int((time.time() - s1_t0) * 1000),
        "details": s1_message,
        "items_checked": {
            "fields_present": len(s1_missing) == 0,
            "incorporation_doc": has_cert_inc,
            "tax_doc": has_tax_doc,
            "bank_proof": has_bank_proof,
        },
    })

    # ══════════════════════════════════════════════════════════════
    # STAGE 2: Tax Registration & Jurisdictional Syntax
    # ══════════════════════════════════════════════════════════════
    s2_t0 = time.time()
    s2_status = "PASSED"
    s2_details = ""

    # Country alignment
    if tax_country and company_country and tax_country != company_country:
        s2_status = "WARNING"
        risk_score += 20
        msg = f"Tax jurisdiction ({tax_country}) diverges from declared incorporation country ({company_country})."
        discrepancies.append({
            "stage": "Tax & Jurisdictional Validity",
            "severity": "HIGH",
            "field": "tax_country",
            "issue": msg,
            "remediation": "Verify corporate nexus or supply proof of foreign branch registration.",
        })
        s2_details = msg

    # Syntax and campus code validation
    if tax_id and company_country:
        valid_tax, tax_reason = validate_tax_id_syntax(tax_country or company_country, tax_id)
        if not valid_tax:
            s2_status = "FAILED"
            risk_score += 35
            discrepancies.append({
                "stage": "Tax & Jurisdictional Validity",
                "severity": "HIGH",
                "field": "tax_id",
                "issue": tax_reason,
                "remediation": f"Submit correct official Tax Identification Number matching {tax_country or company_country} standards along with Form W-9 / CP-575 / VAT Notice.",
            })
            s2_details = tax_reason
        else:
            if not s2_details:
                s2_details = f"Tax ID verified for jurisdiction {tax_country or company_country}: {tax_reason}."

    stages.append({
        "id": "stage_2_tax",
        "name": "Tax Registration & Jurisdictional Syntax",
        "icon": "🏛️",
        "status": s2_status,
        "duration_ms": int((time.time() - s2_t0) * 1000),
        "details": s2_details or "Tax ID format and jurisdiction validated successfully.",
    })

    # ══════════════════════════════════════════════════════════════
    # STAGE 3: Bank Account Cross-Reference & Entity Matching
    # ══════════════════════════════════════════════════════════════
    s3_t0 = time.time()
    s3_status = "PASSED"
    s3_details = []

    # 1. Name Matching: Company legal name vs Bank account holder name
    sim_ratio = calculate_name_similarity(legal_name, account_holder)

    # Also check DBA if provided
    dba_sim_ratio = calculate_name_similarity(dba_name, account_holder) if dba_name else 0.0
    effective_sim = max(sim_ratio, dba_sim_ratio)

    if effective_sim >= 0.88:
        s3_details.append(f"Entity name matches bank account holder ({int(effective_sim * 100)}% match).")
    elif 0.60 <= effective_sim < 0.88:
        # Edge Case 1: Benign DBA / Operating Subsidiary Mismatch
        s3_status = "WARNING"
        risk_score += 25
        issue = (
            f"Bank account holder ('{account_holder}') does not exactly match legal vendor name ('{legal_name}') "
            f"(Similarity: {int(effective_sim * 100)}%). Possible DBA, trade name, or subsidiary relationship."
        )
        discrepancies.append({
            "stage": "Bank Cross-Reference",
            "severity": "MEDIUM",
            "field": "account_holder_name",
            "issue": issue,
            "remediation": "Provide documented DBA (Doing Business As) filing, business name certificate, or corporate parent-subsidiary linking resolution.",
        })
        s3_details.append(issue)
    else:
        # Severe name divergence (< 60%)
        s3_status = "FAILED"
        risk_score += 50
        issue = (
            f"Severe mismatch between registered vendor name ('{legal_name}') and bank account beneficiary ('{account_holder}'). "
            f"Only {int(effective_sim * 100)}% match. High payment diversion risk."
        )
        discrepancies.append({
            "stage": "Bank Cross-Reference",
            "severity": "CRITICAL",
            "field": "account_holder_name",
            "issue": issue,
            "remediation": "Banking details must be held in the registered legal entity's name. Submit official bank letter.",
        })
        s3_details.append(issue)

    # 2. Banking Country Cross-Check & Offshore Haven Detection
    # Edge Case 2: Country Mismatch & Offshore Secrecy Haven
    effective_bank_country = bank_country
    if iban and len(iban) >= 2:
        iban_prefix_country = iban[:2]
        if iban_prefix_country.isalpha() and not effective_bank_country:
            effective_bank_country = iban_prefix_country

    if effective_bank_country in OFFSHORE_HAVENS:
        haven_name = OFFSHORE_HAVENS[effective_bank_country]
        s3_status = "FAILED"
        risk_score += 45
        haven_issue = (
            f"Bank account routing is in {haven_name} ({effective_bank_country}), an internationally recognized "
            f"financial secrecy jurisdiction. Entity is incorporated in {company_country}."
        )
        discrepancies.append({
            "stage": "Bank Cross-Reference",
            "severity": "CRITICAL",
            "field": "bank_country",
            "issue": haven_issue,
            "remediation": "Company policy prohibits disbursements to unverified financial secrecy havens without executive CFO and Legal AML sign-off.",
        })
        s3_details.append(haven_issue)
    elif effective_bank_country and company_country and effective_bank_country != company_country:
        s3_status = "WARNING" if s3_status != "FAILED" else "FAILED"
        risk_score += 20
        cross_border_issue = (
            f"Cross-border banking mismatch: Vendor registered in {company_country}, but bank account is located in {effective_bank_country}."
        )
        discrepancies.append({
            "stage": "Bank Cross-Reference",
            "severity": "MEDIUM",
            "field": "bank_country",
            "issue": cross_border_issue,
            "remediation": "Provide business justification for foreign treasury account along with international tax treaty documentation.",
        })
        s3_details.append(cross_border_issue)

    stages.append({
        "id": "stage_3_bank",
        "name": "Bank Account Cross-Reference & Entity Matching",
        "icon": "🏦",
        "status": s3_status,
        "duration_ms": int((time.time() - s3_t0) * 1000),
        "details": " | ".join(s3_details) if s3_details else "Bank entity cross-reference validated.",
        "name_similarity_pct": int(effective_sim * 100),
    })

    # ══════════════════════════════════════════════════════════════
    # STAGE 4: Risk, AML & Fraud Screening
    # ══════════════════════════════════════════════════════════════
    s4_t0 = time.time()
    s4_status = "PASSED"
    s4_details = []

    # 1. Sanctions & Watchlist Check
    sanction_hit = search_sanctions_watchlist(legal_name)
    if sanction_hit:
        s4_status = "FAILED"
        risk_score += 60
        s_msg = (
            f"WATCHLIST HIT: Entity name matches listing on {sanction_hit['list_source']}. "
            f"Designation: {sanction_hit['reason']}."
        )
        discrepancies.append({
            "stage": "Risk & Sanctions Screening",
            "severity": "CRITICAL",
            "field": "sanctions_watchlist",
            "issue": s_msg,
            "remediation": "Mandatory escalation to Compliance & Legal Officer. Onboarding immediately halted.",
        })
        s4_details.append(s_msg)

    # 2. Duplicate Bank Account Hijacking Check (BEC / Account Takeover)
    # Edge Case 4: Bank account already registered to an existing approved vendor!
    conflicting_vendor = find_conflicting_vendor_bank(iban, account_number, legal_name)
    if conflicting_vendor:
        s4_status = "FAILED"
        risk_score += 60
        hijack_msg = (
            f"CRITICAL FRAUD ALERT: Submitted bank account (IBAN/Account {iban or account_number}) is ALREADY ASSIGNED "
            f"to existing approved vendor '{conflicting_vendor['vendor_name']}' (ID: {conflicting_vendor['id']}). "
            f"Suspected Vendor Email Compromise / Bank Account Takeover attempt!"
        )
        discrepancies.append({
            "stage": "Risk & Sanctions Screening",
            "severity": "CRITICAL",
            "field": "bank_account_duplicate",
            "issue": hijack_msg,
            "remediation": "IMMEDIATE AP FREEZE. Contact existing vendor via verified out-of-band phone number to investigate potential credential or invoice compromise.",
        })
        s4_details.append(hijack_msg)

    # 3. Shell Company & Incorporation Velocity Indicators
    if inc_date_str:
        try:
            inc_date = datetime.strptime(inc_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - inc_date).days
            if days_old < 60:
                risk_score += 20
                age_msg = f"New incorporation velocity: Entity was registered only {days_old} days ago ({inc_date_str})."
                discrepancies.append({
                    "stage": "Risk & Sanctions Screening",
                    "severity": "MEDIUM",
                    "field": "incorporation_date",
                    "issue": age_msg,
                    "remediation": "Require upfront financial statements or proof of prior operational history.",
                })
                s4_details.append(age_msg)
        except Exception:
            pass

    # Generic mail domain check
    if contact_email:
        domain = contact_email.split("@")[-1].lower() if "@" in contact_email else ""
        if domain in GENERIC_MAIL_DOMAINS:
            risk_score += 15
            mail_msg = f"Vendor uses generic/free email service (@{domain}) instead of corporate corporate domain."
            discrepancies.append({
                "stage": "Risk & Sanctions Screening",
                "severity": "LOW",
                "field": "contact_email",
                "issue": mail_msg,
                "remediation": "Request contact from official corporate domain or provide identity verification for signer.",
            })
            s4_details.append(mail_msg)

    stages.append({
        "id": "stage_4_fraud",
        "name": "Risk, AML & Fraud Screening",
        "icon": "🛡️",
        "status": s4_status,
        "duration_ms": int((time.time() - s4_t0) * 1000),
        "details": " | ".join(s4_details) if s4_details else "Passed sanctions, duplicate bank screening, and shell indicators.",
    })

    # ══════════════════════════════════════════════════════════════
    # STAGE 5: Autonomous Decision & Action Synthesizer
    # ══════════════════════════════════════════════════════════════
    s5_t0 = time.time()
    risk_score = min(100, max(0, risk_score))

    # Determine risk tier
    if risk_score <= 20:
        risk_tier = "LOW"
    elif risk_score <= 50:
        risk_tier = "MEDIUM"
    elif risk_score <= 75:
        risk_tier = "HIGH"
    else:
        risk_tier = "CRITICAL"

    # Determine status: APPROVED, PENDING_VENDOR_ACTION, REJECTED
    has_critical_discrepancy = any(d["severity"] == "CRITICAL" for d in discrepancies)
    has_actionable_issues = any(d["severity"] in ("MEDIUM", "HIGH") for d in discrepancies)

    if has_critical_discrepancy or risk_score >= 60:
        final_status = "REJECTED"
        summary = f"Onboarding Rejected — {len([d for d in discrepancies if d['severity'] == 'CRITICAL'])} critical security/compliance blocker(s) detected (Risk Score: {risk_score}/100)."
    elif has_actionable_issues or s1_status != "PASSED" or s2_status != "PASSED" or s3_status != "PASSED":
        final_status = "PENDING_VENDOR_ACTION"
        summary = f"Pending Vendor Action — Clarifications and documentation required for {len(discrepancies)} identified inconsistency/inconsistencies (Risk Score: {risk_score}/100)."
    else:
        final_status = "APPROVED"
        summary = f"Vendor Approved — All verifications satisfied, low risk profile (Score: {risk_score}/100). Ready for ERP & payment activation."

    # Generate Targeted Vendor Communication (if not approved)
    vendor_email = None
    if final_status in ("PENDING_VENDOR_ACTION", "REJECTED"):
        vendor_email = generate_vendor_communication(
            status=final_status,
            vendor_name=legal_name or "Vendor Partner",
            contact_name=company_info.get("contact_name", "Accounts Receivable Team"),
            discrepancies=discrepancies,
        )

    # Generate Procurement AP Audit Memo
    audit_memo = generate_ap_audit_memo(
        vendor_name=legal_name,
        country=company_country,
        tax_id=tax_id,
        status=final_status,
        risk_score=risk_score,
        risk_tier=risk_tier,
        stages=stages,
        discrepancies=discrepancies,
    )

    stages.append({
        "id": "stage_5_decision",
        "name": "Autonomous Decision & Action Plan",
        "icon": "⚖️",
        "status": "PASSED" if final_status == "APPROVED" else ("WARNING" if final_status == "PENDING_VENDOR_ACTION" else "FAILED"),
        "duration_ms": int((time.time() - s5_t0) * 1000),
        "details": f"Final Status: {final_status} | Risk Score: {risk_score}/100 ({risk_tier} Risk).",
    })

    total_duration_ms = int((time.time() - start_time) * 1000)

    return {
        "status": final_status,
        "risk_score": risk_score,
        "risk_tier": risk_tier,
        "summary": summary,
        "stages": stages,
        "discrepancies": discrepancies,
        "vendor_email": vendor_email,
        "audit_note": audit_memo,
        "total_duration_ms": total_duration_ms,
        "metadata": {
            "vendor_name": legal_name,
            "country": company_country,
            "tax_id": tax_id,
            "bank_name": bank_name,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def generate_vendor_communication(
    status: str,
    vendor_name: str,
    contact_name: str,
    discrepancies: List[Dict[str, Any]],
) -> str:
    """Generate a professional, actionable email to communicate back what is needed."""
    if status == "PENDING_VENDOR_ACTION":
        items_bullets = []
        for i, d in enumerate(discrepancies, 1):
            items_bullets.append(
                f"{i}. [{d['stage']}] {d['issue']}\n   Action Required: {d['remediation']}"
            )
        bullets_text = "\n\n".join(items_bullets)

        return f"""Subject: Action Required: Completing Your Vendor Onboarding with Our Finance Team

Dear {contact_name or vendor_name},

Thank you for submitting your vendor onboarding registration for {vendor_name}. 

To ensure compliance with enterprise procurement guidelines and ensure timely payment disbursement, our automated verification system conducted an initial review. Before we can finalize your account activation in our ERP, please address the following items:

{bullets_text}

How to submit these updates:
1. Please log in to your secure vendor onboarding portal or reply directly to this email with the requested documentation attached.
2. Ensure all attached documents are official PDFs issued within the last 90 days.

Once received, your account will be re-evaluated within 1 business day. If you have any questions or believe any information was flagged in error, please let us know.

Best regards,

Accounts Payable & Vendor Onboarding Team
Global Procurement Operations
"""
    else:  # REJECTED
        critical_items = [d['issue'] for d in discrepancies if d['severity'] == 'CRITICAL']
        reasons_text = "\n- ".join(critical_items) if critical_items else "Compliance and banking verification criteria were not satisfied."

        return f"""Subject: Notice Regarding Your Vendor Onboarding Application — {vendor_name}

Dear {contact_name or vendor_name},

Thank you for your interest in partnering with our organization.

We have completed the mandatory compliance and financial verification review for {vendor_name}. At this time, we are unable to approve your vendor onboarding application due to inconsistencies identified during our statutory screening:

- {reasons_text}

In accordance with our enterprise risk and AML policies, payments cannot be authorized to accounts with unresolved discrepancies of this nature.

If you believe this determination was reached in error or wish to submit an appeal with official corporate legal documentation, please contact our Compliance Office at compliance-review@enterprise-procure.com.

Sincerely,

Vendor Risk & Compliance Committee
Global Procurement Operations
"""


def generate_ap_audit_memo(
    vendor_name: str,
    country: str,
    tax_id: str,
    status: str,
    risk_score: int,
    risk_tier: str,
    stages: List[Dict[str, Any]],
    discrepancies: List[Dict[str, Any]],
) -> str:
    """Generate an official internal audit memorandum for AP team and SOX compliance."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    discrepancy_lines = []
    if discrepancies:
        for d in discrepancies:
            discrepancy_lines.append(f"  * [{d['severity']}] {d['stage']}: {d['issue']}")
    else:
        discrepancy_lines.append("  * None. All verification checks passed clean.")

    stage_summaries = []
    for s in stages:
        stage_summaries.append(f"  * {s['name']}: {s['status']} ({s['duration_ms']}ms) — {s['details']}")

    return f"""================================================================================
PROCUREMENT COMPLIANCE AUDIT MEMORANDUM (SOX-404)
================================================================================
Timestamp:          {timestamp}
Entity Legal Name:  {vendor_name}
Jurisdiction:       {country} | Tax ID: {tax_id}
Final Status:       {status}
Calculated Risk:    {risk_score}/100 [{risk_tier} RISK TIER]
--------------------------------------------------------------------------------
STAGE-BY-STAGE VERIFICATION LOG:
{chr(10).join(stage_summaries)}

DISCREPANCIES & FINDINGS:
{chr(10).join(discrepancy_lines)}

OPERATIONAL DIRECTIVE:
{
  "Authorized for automated ERP vendor master creation and immediate purchase order generation."
  if status == "APPROVED" else (
  "Account placed in HOLD. Automated vendor notification dispatched. Awaiting supplemental documentation before reactivation."
  if status == "PENDING_VENDOR_ACTION" else
  "BLOCKED. Vendor profile quarantined. Do not issue payment. Escalate to Corporate Security / Legal."
)
}
================================================================================
"""
