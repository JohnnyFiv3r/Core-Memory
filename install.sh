#!/usr/bin/env bash
set -euo pipefail

# Core Memory installer (workspace-local)
# Usage: ./install.sh [workspace]

WORKSPACE="${1:-$HOME/.openclaw/workspace}"
cd "$WORKSPACE"

echo "Installing Core Memory in: $WORKSPACE"

if [ ! -f pyproject.toml ]; then
  echo "pyproject.toml not found in $WORKSPACE"
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install -e .

mkdir -p "$WORKSPACE/.beads/events" "$WORKSPACE/.turns"

echo "✓ core-memory installed"
echo ""
echo "Quick test:"
echo "  $WORKSPACE/.venv/bin/core-memory --root $WORKSPACE stats"
