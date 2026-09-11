#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=/usr/bin/python3
fi

if [[ ! -x .venv/bin/pdftoepub ]]; then
  echo "Setting up PdfToEpub for the first time..."
  "$PYTHON" -m venv .venv
  .venv/bin/pip install -e .
fi

if [[ $# -eq 0 ]]; then
  if curl -sf -o /dev/null --connect-timeout 1 "http://127.0.0.1:8765/" \
    || lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Converter already running. Opening http://127.0.0.1:8765"
    open "http://127.0.0.1:8765"
    exit 0
  fi
  echo "Opening the converter in your browser..."
  exec .venv/bin/pdftoepub serve --open
fi

exec .venv/bin/pdftoepub convert --open "$@"
