"""
CLI verification test runner for Vendor Onboarding Verification System.
Executes Happy Path + all 4 defined Edge Cases and prints rich terminal output.
"""

import json
import os
import sys

# Ensure parent directory is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CURRENT_DIR))

try:
    from vendor_onboarding_system.db import init_db, save_run
    from vendor_onboarding_system.engine import verify_vendor_submission
except (ImportError, ValueError):
    from db import init_db, save_run
    from engine import verify_vendor_submission


def run_tests():
    print("=" * 80)
    print("🚀 VENDOR ONBOARDING VERIFICATION SYSTEM — TEST HARNESS")
    print("=" * 80)

    init_db()

    presets_path = os.path.join(CURRENT_DIR, "test_cases.json")
    with open(presets_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    presets = data["presets"]
    passed_all = True

    for idx, preset in enumerate(presets, 1):
        print(f"\n--- Scenario {idx}: {preset['title']} ---")
        print(f"Subtitle:    {preset['subtitle']}")
        print(f"Description: {preset['description']}")

        payload = preset["payload"]
        result = verify_vendor_submission(payload)

        status = result["status"]
        risk_score = result["risk_score"]
        risk_tier = result["risk_tier"]
        duration = result["total_duration_ms"]
        expected_status = preset["expected_status"]

        status_icon = "✅" if status == expected_status else "❌"
        print(f"Result Status:   {status_icon} {status} (Expected: {expected_status})")
        print(f"Risk Score:      {risk_score}/100 [{risk_tier} RISK]")
        print(f"Processing Time: {duration} ms")
        print(f"Summary:         {result['summary']}")

        if result["discrepancies"]:
            print("Flagged Issues:")
            for d in result["discrepancies"]:
                print(f"  * [{d['severity']}] {d['stage']}: {d['issue']}")

        if result.get("vendor_email"):
            first_line = result["vendor_email"].strip().split("\n")[0]
            print(f"Generated Email: '{first_line}' ({len(result['vendor_email'])} characters)")

        if status != expected_status:
            passed_all = False

        # Save run into database
        save_run(
            run_id=f"test_run_{preset['id']}",
            vendor_name=payload["company_info"]["legal_name"],
            country=payload["company_info"]["country"],
            tax_id=payload["tax_info"]["tax_id"],
            status=result["status"],
            risk_score=result["risk_score"],
            risk_tier=result["risk_tier"],
            summary=result["summary"],
            stages=result["stages"],
            discrepancies=result["discrepancies"],
            vendor_email=result.get("vendor_email"),
            audit_note=result.get("audit_note"),
            input_payload=payload,
            total_duration_ms=result["total_duration_ms"],
        )

    print("\n" + "=" * 80)
    if passed_all:
        print("🎉 ALL TEST SCENARIOS (HAPPY PATH + 4 EDGE CASES) PASSED WITH EXPECTED BEHAVIOR!")
    else:
        print("⚠️ Some scenarios did not match expected statuses.")
    print("=" * 80)


if __name__ == "__main__":
    run_tests()
