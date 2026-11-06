"""
High-performance, zero-dependency HTTP Web Server for Vendor Onboarding System.
Provides REST APIs, Server-Sent Events (SSE) for live stage-by-stage execution,
and static frontend serving.
"""

import json
import logging
import os
import sys
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

# Ensure local imports work cleanly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from vendor_onboarding_system.db import (
        get_all_runs,
        get_dashboard_stats,
        get_run_by_id,
        init_db,
        save_run,
    )
    from vendor_onboarding_system.engine import verify_vendor_submission
except (ImportError, ValueError):
    from db import (
        get_all_runs,
        get_dashboard_stats,
        get_run_by_id,
        init_db,
        save_run,
    )
    from engine import verify_vendor_submission

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("VendorServer")

STATIC_DIR = os.path.join(CURRENT_DIR, "static")
PRESETS_FILE = os.path.join(CURRENT_DIR, "test_cases.json")


class VendorRequestHandler(SimpleHTTPRequestHandler):
    """Handles API requests and UI assets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        # Clean logging
        logger.info("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        if path == "" or path == "/index.html":
            index_path = os.path.join(STATIC_DIR, "index.html")
            with open(index_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        elif path == "/api/presets":
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                presets = json.load(f)
            self._send_json(presets)
            return

        elif path == "/api/stats":
            stats = get_dashboard_stats()
            self._send_json(stats)
            return

        elif path == "/api/runs":
            params = urllib.parse.parse_qs(url.query)
            limit = int(params.get("limit", ["100"])[0])
            offset = int(params.get("offset", ["0"])[0])
            runs = get_all_runs(limit=limit, offset=offset)
            self._send_json({"runs": runs})
            return

        elif path.startswith("/api/runs/"):
            run_id = path.split("/api/runs/")[-1]
            run = get_run_by_id(run_id)
            if run:
                self._send_json(run)
            else:
                self._send_json({"error": "Run not found"}, status=404)
            return

        elif path == "/api/export":
            runs = get_all_runs(limit=500)
            # CSV export
            import io
            import csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Run ID", "Timestamp", "Vendor Name", "Country", "Tax ID", "Status", "Risk Score", "Risk Tier", "Duration (ms)", "Summary"])
            for r in runs:
                writer.writerow([r["id"], r["created_at"], r["vendor_name"], r["country"], r["tax_id"], r["status"], r["risk_score"], r["risk_tier"], r["total_duration_ms"], r["summary"]])
            csv_data = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=vendor_onboarding_audit.csv")
            self.send_header("Content-Length", str(len(csv_data)))
            self.end_headers()
            self.wfile.write(csv_data)
            return

        # Default static file serving
        super().do_GET()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(post_body) if post_body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return

        if path == "/api/verify":
            # Direct synchronous verification
            result = verify_vendor_submission(payload)
            run_id = f"run_{uuid.uuid4().hex[:10]}"
            vendor_name = payload.get("company_info", {}).get("legal_name", "Unknown Entity")
            country = payload.get("company_info", {}).get("country", "US")
            tax_id = payload.get("tax_info", {}).get("tax_id", "")

            save_run(
                run_id=run_id,
                vendor_name=vendor_name,
                country=country,
                tax_id=tax_id,
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
            result["run_id"] = run_id
            self._send_json(result)
            return

        elif path == "/api/verify/stream":
            # Live Server-Sent Events (SSE) streaming for interview visualizer
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def send_event(event_name: str, event_data: dict):
                msg = f"event: {event_name}\ndata: {json.dumps(event_data)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()

            run_id = f"run_{uuid.uuid4().hex[:10]}"
            vendor_name = payload.get("company_info", {}).get("legal_name", "Unknown Entity")

            send_event("started", {"run_id": run_id, "vendor_name": vendor_name, "message": "Starting verification pipeline..."})
            time.sleep(0.15)

            # Execute full logic
            result = verify_vendor_submission(payload)

            # Stream each stage with slight intentional pacing for visual demo clarity
            for stage in result["stages"]:
                # Notify stage started
                send_event("stage_progress", {
                    "stage_id": stage["id"],
                    "name": stage["name"],
                    "icon": stage["icon"],
                    "state": "RUNNING",
                    "message": f"Executing {stage['name']}...",
                })
                time.sleep(0.25)

                # Notify stage completed
                send_event("stage_completed", {
                    "stage_id": stage["id"],
                    "name": stage["name"],
                    "icon": stage["icon"],
                    "state": stage["status"],
                    "duration_ms": stage["duration_ms"],
                    "details": stage["details"],
                    "stage_data": stage,
                })
                time.sleep(0.1)

            # Save to database
            country = payload.get("company_info", {}).get("country", "US")
            tax_id = payload.get("tax_info", {}).get("tax_id", "")
            save_run(
                run_id=run_id,
                vendor_name=vendor_name,
                country=country,
                tax_id=tax_id,
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

            result["run_id"] = run_id
            send_event("pipeline_complete", result)
            return

        self._send_json({"error": "Endpoint not found"}, status=404)


def run_server(port: int = 8080):
    init_db()
    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, VendorRequestHandler)
    print("=" * 80)
    print(f"🛡️  VENDOR ONBOARDING VERIFICATION SYSTEM IS LIVE!")
    print(f"👉 Local Web UI:      http://localhost:{port}")
    print(f"👉 Dashboard & Runs:  http://localhost:{port}/api/runs")
    print(f"👉 KPI Metrics:       http://localhost:{port}/api/stats")
    print("=" * 80)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server gracefully...")
        httpd.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(port)
