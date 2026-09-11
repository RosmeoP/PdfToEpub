#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/pdftoepub" ]]; then
  exec "$ROOT/.venv/bin/pdftoepub" uninstall-service "$@"
fi
if command -v pdftoepub >/dev/null 2>&1; then
  exec pdftoepub uninstall-service "$@"
fi
exec python3 -m pdftoepub uninstall-service "$@"
