#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/pdftoepub" ]]; then
  exec "$ROOT/.venv/bin/pdftoepub" install-service "$@"
fi
if command -v pdftoepub >/dev/null 2>&1; then
  exec pdftoepub install-service "$@"
fi
exec python3 -m pdftoepub install-service "$@"
