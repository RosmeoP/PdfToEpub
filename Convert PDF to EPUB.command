#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/pdftoepub ]]; then
  echo "Setting up PdfToEpub for the first time..."
  python3 -m venv .venv
  .venv/bin/pip install -e .
fi

if [[ $# -eq 0 ]]; then
  echo "Opening the converter in your browser..."
  exec .venv/bin/pdftoepub serve --open
fi

exec .venv/bin/pdftoepub convert --open "$@"
