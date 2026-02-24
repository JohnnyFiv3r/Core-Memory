#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${WEBFLOW_TOKEN:-}" ]]; then
  echo "ERROR: WEBFLOW_TOKEN is required for local mode"
  echo "Set it first, e.g.: export WEBFLOW_TOKEN=..."
  exit 1
fi

exec npx -y webflow-mcp-server@latest
