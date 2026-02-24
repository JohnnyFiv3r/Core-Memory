#!/usr/bin/env bash
set -euo pipefail

# Webflow hosted MCP endpoint via mcp-remote.
# Use this in clients that support command-based MCP servers.

exec npx -y mcp-remote https://mcp.webflow.com/sse
