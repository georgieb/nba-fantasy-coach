#!/usr/bin/env bash
# Starts backend (:8000) and frontend (:3000). Ctrl-C stops both.
set -e
cd "$(dirname "$0")"
if [ ! -d backend/.venv ]; then
  python3.12 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd frontend && npm run dev -- -H 127.0.0.1   # localhost only: not reachable from your Wi-Fi
