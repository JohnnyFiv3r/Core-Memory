#!/usr/bin/env bash
set -euo pipefail

need_node_major=22
need_node_minor=3

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node is not installed"
  exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "ERROR: npx is not installed"
  exit 1
fi

node_version_raw="$(node -v)"
node_version="${node_version_raw#v}"
major="${node_version%%.*}"
rest="${node_version#*.}"
minor="${rest%%.*}"

if (( major < need_node_major )) || { (( major == need_node_major )) && (( minor < need_node_minor )); }; then
  echo "ERROR: Node.js ${need_node_major}.${need_node_minor}+ required, found ${node_version_raw}"
  exit 1
fi

echo "OK: node=${node_version_raw} npx=$(npx --version)"
echo "Prereqs satisfied for webflow-mcp-server."
