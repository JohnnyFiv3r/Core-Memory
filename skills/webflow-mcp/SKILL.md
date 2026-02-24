---
name: webflow-mcp
description: Set up and operate the Webflow MCP server in remote OAuth mode or local token mode. Use when connecting AI tooling to Webflow sites, configuring MCP client JSON, troubleshooting Webflow MCP startup/auth, or running Webflow MCP helper scripts.
---

# Webflow MCP

Use this skill to configure Webflow MCP safely without mutating unrelated OpenClaw settings.

## Workflow

1. Verify prerequisites:

```bash
skills/webflow-mcp/scripts/check-prereqs.sh
```

2. Pick mode:

- **Remote OAuth mode** (recommended first): Webflow-hosted endpoint via `mcp-remote`.
- **Local token mode**: `WEBFLOW_TOKEN` + `webflow-mcp-server@latest`.

3. Apply the correct MCP client snippet from:

- `references/client-config-snippets.md`

4. For Webflow Designer interaction, ensure the **Webflow MCP Bridge App** is published and opened in the Designer Apps panel.

## Run commands

### Remote mode

```bash
skills/webflow-mcp/scripts/run-remote.sh
```

### Local mode

```bash
export WEBFLOW_TOKEN="..."
skills/webflow-mcp/scripts/run-local.sh
```

## Guardrails

- Do not edit unrelated gateway/config files while setting up Webflow MCP.
- Do not delete auth cache unless explicitly asked.
- Prefer remote OAuth mode before local token mode when possible.
- Keep secrets out of git-tracked files.

## Quick troubleshooting

- Node version error: upgrade to Node 22.3+
- Server not starting: run `check-prereqs.sh`, then retry mode command
- OAuth loops/failures: re-open MCP client and re-authorize
- Designer not responding: verify Bridge App is published and running
