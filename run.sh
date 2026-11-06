#!/usr/bin/env bash
set -e
PORT=${PORT:-8080}
cd "$(dirname "$0")"

echo "================================================================="
echo "🛡️  STARTING VENDOR ONBOARDING VERIFICATION SYSTEM"
echo "================================================================="
python3 test_cli.py
echo ""
echo "Starting web server on port ${PORT}..."
exec python3 server.py
