#!/usr/bin/env bash
# Daily paper-trading run: set up deps if needed, advance the ledger to the
# latest close, and append the output to paper/log.md. Never places real orders.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

out=$(.venv/bin/python -m trading_bot paper "$@")
echo "$out"

mkdir -p paper
{
  echo "## Run $(date -u +'%Y-%m-%d %H:%M UTC')"
  echo
  echo '```'
  echo "$out"
  echo '```'
  echo
} >> paper/log.md
