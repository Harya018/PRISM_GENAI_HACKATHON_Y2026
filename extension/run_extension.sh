#!/usr/bin/env bash
# One command to run the Samsung device-troubleshooting extension (Section B).
#
#   ./extension/run_extension.sh
#
# Requires the same .env as the benchmark agent (repo root — LIVEKIT_URL/API_KEY/API_SECRET,
# GOOGLE_API_KEY) and the same dependencies already installed for the main agent (no extra
# packages: this extension reuses agent/commit_gate.py and agent/resolver.py as-is).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "${ROOT}/.env" ]; then
  echo "Missing .env in ${ROOT} — copy .env.example, fill in LIVEKIT_* and GOOGLE_API_KEY." >&2
  exit 1
fi

VENV="${ROOT}/.venv"
if [ -d "${VENV}" ]; then
  PY="${VENV}/bin/python3"
else
  PY="python3"
fi

cd "${ROOT}"
exec "${PY}" extension/device_agent.py start
