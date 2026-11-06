"""
Database module for Vendor Onboarding Verification System.
Handles SQLite storage for:
1. Past verification runs and audit trail
2. Known approved ERP vendors (for duplicate detection & account takeover defense)
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_onboarding.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize database tables and seed baseline known enterprise vendors."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Table: Past verification runs
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                vendor_name TEXT NOT NULL,
                country TEXT NOT NULL,
                tax_id TEXT,
                status TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                risk_tier TEXT NOT NULL,
                summary TEXT NOT NULL,
                stages_json TEXT NOT NULL,
                discrepancies_json TEXT NOT NULL,
                vendor_email TEXT,
                audit_note TEXT,
                input_payload_json TEXT NOT NULL,
                total_duration_ms INTEGER NOT NULL
            )
            """
        )

        # Table: Known approved ERP vendors (simulating enterprise SAP / NetSuite record)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS known_vendors (
                id TEXT PRIMARY KEY,
                vendor_name TEXT NOT NULL,
                country TEXT NOT NULL,
                tax_id TEXT NOT NULL,
                bank_name TEXT NOT NULL,
                bank_account_number TEXT,
                iban TEXT,
                swift_bic TEXT,
                contact_email TEXT,
                status TEXT NOT NULL,
                approved_at TEXT NOT NULL
            )
            """
        )

        # Table: Watchlist / Sanctions screening database (simulating OFAC SDN / EU sanctions list)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sanctions_watchlist (
                id TEXT PRIMARY KEY,
                entity_name TEXT NOT NULL,
                country TEXT,
                list_source TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )

        # Seed known enterprise vendors if empty
        cursor.execute("SELECT COUNT(*) FROM known_vendors")
        if cursor.fetchone()[0] == 0:
            seed_vendors = [
                (
                    "vnd_001",
                    "Global Supplies Inc",
                    "US",
                    "12-3456789",
                    "JPMorgan Chase",
                    "882390142",
                    "US33CHAS882390142",
                    "CHASUS33",
                    "billing@globalsupplies.com",
                    "ACTIVE_APPROVED",
                    "2025-01-15T09:00:00Z",
                ),
                (
                    "vnd_002",
                    "CloudScale Networks LLC",
                    "US",
                    "98-7654321",
                    "Silicon Valley Bank",
                    "449102837",
                    "US55SVBK449102837",
                    "SVBKUS6S",
                    "ap@cloudscale.io",
                    "ACTIVE_APPROVED",
                    "2025-03-20T11:30:00Z",
                ),
                (
                    "vnd_003",
                    "EuroTech Industrial Solutions SE",
                    "DE",
                    "DE814529810",
                    "Deutsche Bank AG",
                    "DE89370400440532013000",
                    "DE89370400440532013000",
                    "DEUTDEDDFXX",
                    "finance@eurotech-se.de",
                    "ACTIVE_APPROVED",
                    "2024-11-10T14:15:00Z",
                ),
            ]
            cursor.executemany(
                """
                INSERT INTO known_vendors (
                    id, vendor_name, country, tax_id, bank_name,
                    bank_account_number, iban, swift_bic, contact_email,
                    status, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                seed_vendors,
            )

        # Seed sanctions watchlist if empty
        cursor.execute("SELECT COUNT(*) FROM sanctions_watchlist")
        if cursor.fetchone()[0] == 0:
            seed_sanctions = [
                ("snc_001", "Gazprom Neft Trading", "RU", "OFAC_SDN", "Sectoral sanctions"),
                ("snc_002", "Al-Baraka Financial Services", "SY", "OFAC_SDN", "Terrorist financing designation"),
                ("snc_003", "Trans-Atlantic Maritime Export", "IR", "EU_SANCTIONS", "Weapons proliferation"),
                ("snc_004", "Vanguard Global Advisory Ltd", "SC", "HIGH_RISK_FINANCIAL_SECRECY", "Shell company nexus in secrecy jurisdiction"),
            ]
            cursor.executemany(
                """
                INSERT INTO sanctions_watchlist (
                    id, entity_name, country, list_source, reason
                ) VALUES (?, ?, ?, ?, ?)
                """,
                seed_sanctions,
            )

        conn.commit()


def save_run(
    run_id: str,
    vendor_name: str,
    country: str,
    tax_id: str,
    status: str,
    risk_score: int,
    risk_tier: str,
    summary: str,
    stages: List[Dict[str, Any]],
    discrepancies: List[Dict[str, Any]],
    vendor_email: Optional[str],
    audit_note: Optional[str],
    input_payload: Dict[str, Any],
    total_duration_ms: int,
) -> None:
    """Save an execution run into the SQLite runs table."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO runs (
                id, created_at, vendor_name, country, tax_id,
                status, risk_score, risk_tier, summary,
                stages_json, discrepancies_json, vendor_email,
                audit_note, input_payload_json, total_duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                vendor_name,
                country,
                tax_id,
                status,
                risk_score,
                risk_tier,
                summary,
                json.dumps(stages),
                json.dumps(discrepancies),
                vendor_email,
                audit_note,
                json.dumps(input_payload),
                total_duration_ms,
            ),
        )
        conn.commit()


def get_all_runs(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Retrieve runs sorted by most recent."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, vendor_name, country, tax_id,
                   status, risk_score, risk_tier, summary,
                   stages_json, discrepancies_json, vendor_email,
                   audit_note, total_duration_ms
            FROM runs
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["stages"] = json.loads(item.pop("stages_json"))
            item["discrepancies"] = json.loads(item.pop("discrepancies_json"))
            result.append(item)
        return result


def get_run_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full run details including input payload."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM runs WHERE id = ?
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["stages"] = json.loads(item.pop("stages_json"))
        item["discrepancies"] = json.loads(item.pop("discrepancies_json"))
        item["input_payload"] = json.loads(item.pop("input_payload_json"))
        return item


def get_dashboard_stats() -> Dict[str, Any]:
    """Calculate aggregate KPIs across all runs."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_submissions,
                SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved_count,
                SUM(CASE WHEN status = 'PENDING_VENDOR_ACTION' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected_count,
                AVG(risk_score) as avg_risk_score,
                AVG(total_duration_ms) as avg_latency_ms
            FROM runs
            """
        )
        row = cursor.fetchone()
        total = row["total_submissions"] or 0
        approved = row["approved_count"] or 0
        pending = row["pending_count"] or 0
        rejected = row["rejected_count"] or 0
        avg_risk = round(row["avg_risk_score"] or 0, 1)
        avg_latency = round(row["avg_latency_ms"] or 0, 0)

        approval_rate = round((approved / total * 100), 1) if total > 0 else 0.0

        return {
            "total_submissions": total,
            "approved_count": approved,
            "pending_count": pending,
            "rejected_count": rejected,
            "approval_rate": approval_rate,
            "avg_risk_score": avg_risk,
            "avg_latency_ms": avg_latency,
        }


def find_conflicting_vendor_bank(iban: str, account_number: str, current_vendor_name: str) -> Optional[Dict[str, Any]]:
    """
    Check if the submitted IBAN or Bank Account Number is already registered
    under a different existing vendor. This prevents Vendor Email Compromise (BEC)
    and unauthorized bank diversion hijacking.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        # Query for matches on IBAN or account number
        cursor.execute(
            """
            SELECT * FROM known_vendors
            WHERE (
                (iban IS NOT NULL AND iban != '' AND iban = ?)
                OR
                (bank_account_number IS NOT NULL AND bank_account_number != '' AND bank_account_number = ?)
            )
            """,
            (iban.strip().replace(" ", "").upper(), account_number.strip()),
        )
        row = cursor.fetchone()
        if row:
            vendor = dict(row)
            # If names differ substantially, flag conflict
            if vendor["vendor_name"].strip().lower() != current_vendor_name.strip().lower():
                return vendor
        return None


def search_sanctions_watchlist(name: str) -> Optional[Dict[str, Any]]:
    """Search for exact or close name in sanctions database."""
    name_clean = name.strip().lower()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sanctions_watchlist")
        for row in cursor.fetchall():
            target = row["entity_name"].strip().lower()
            if target in name_clean or name_clean in target:
                return dict(row)
    return None
