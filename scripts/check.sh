#!/usr/bin/env bash
# Run the test suite locally (no network, no uploads).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/pytest" ]]; then
  exec "$ROOT/.venv/bin/pytest" -q "$@"
fi
exec pytest -q "$@"
