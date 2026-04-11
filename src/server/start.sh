#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -f requirements.txt ]; then
  echo "Run from the src/server directory."
  exit 1
fi

# Install deps if needed
pip3 install -q -r requirements.txt

echo ""
echo "  Policy Maker"
echo "  ─────────────────────────────"
echo "  http://localhost:8080"
echo "  Password: ZPR"
echo ""

uvicorn server:app --reload --port 8080
