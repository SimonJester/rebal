#!/usr/bin/env bash
# Download the latest safe project files from GitHub into this folder.
# Does not touch local-only files (settings.json, real portfolio CSVs, .scratch/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "Git is not installed (or not on your PATH)."
  echo "Install Git, then run this script again. See docs/HOW-TO-UPDATE.md."
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "This folder is not a git repository."
  echo "Open the rebal project folder (the one that contains .git), then run again."
  exit 1
fi

echo "Updating rebal from GitHub..."
echo "Folder: $ROOT"
echo

if git pull; then
  echo
  echo "Update finished."
  echo "Your private files (settings.json, real portfolio CSVs, .scratch/) were not"
  echo "downloaded from GitHub — they stay only on this computer if you already have them."
  echo
  echo "Next: activate the venv if needed, then run:  python rebal.py"
  echo "Full guide: docs/HOW-TO-UPDATE.md"
else
  echo
  echo "Update did not complete. Common fixes are in docs/HOW-TO-UPDATE.md"
  echo "(search for \"If something goes wrong\")."
  exit 1
fi
