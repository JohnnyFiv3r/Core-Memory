# Core Memory MCP Quickstart

Status: v1 protocol surface

Core Memory exposes an MCP streamable-HTTP server at `/mcp`. The REST `/v1/*` endpoints remain unchanged; MCP is a peer adapter surface for MCP-capable agent clients.

## Install

```bash
pip install "core-memory[mcp]"
core-memory mcp version
```

The v1 SDK dependency is pinned as `mcp>=1.27.1,<2`. `core-memory mcp version` reports both the Core Memory MCP pin and the installed SDK package version.

## Run the HTTP/MCP server

By default, the MCP server uses this store root:

```text
~/.core-memory/store
```

Override it with `CORE_MEMORY_ROOT`:

```bash
export CORE_MEMORY_ROOT="$HOME/.core-memory/store"
export CORE_MEMORY_HTTP_PORT=8000
python -m core_memory.integrations.http.server
```

Health checks:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/mcp/healthz
core-memory mcp status
```

## Install into a client

```bash
core-memory mcp install --client cursor --root ~/.core-memory/store --port 8000
```

Supported client names:

- `claude-code`
- `cursor`
- `windsurf`
- `open-webui`

With no `--client`, Core Memory attempts to detect supported client config files. If none are found, the command prints a manual JSON fallback like:

```json
{
  "mcpServers": {
    "core-memory": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Use `--no-start` if you want Core Memory to write client config but not start or restart the local service. Internal dry-run hooks exist for tests, but dry-run is not a documented user workflow.

## MCP tools

### `capture`

Writes observed conversation turns through the canonical Core Memory write boundary. Provide either `turns` or `{user, assistant, as_user?, as_assistant?}`.

### `sync_transcript_snapshot`

Periodically syncs the current visible, user-authorized conversation transcript through the canonical ingest/capture path. Use it only after explicit user/app opt-in, and pass `user_opted_in=true` plus a stable `conversation_id`, `session_id`, or `transcript_id` on every successful call. Once sync is enabled, use it as a safety net for long chats: after meaningful milestones, after important decisions or preference changes, periodically in long conversations, before compaction, or when the user asks to sync the conversation.

Do not call it when sync is not enabled, when the user has opted out, or when opt-in is unclear; ask first. Keep the stable conversation/session identity the same across snapshots for the same conversation so replay remains idempotent. Include only visible conversation content intended for memory sync, not hidden instructions, credentials, or unrelated private data. It returns a `transcript_hash` that can be passed as `previous_snapshot_hash` on the next snapshot.

For transcripts that are too long to send in full, use checkpoint mode with `recent_turns` plus `checkpoint_summary`; this is a fallback because it includes model-authored summary content.

### `capture_session`

End-of-session safety net. Replays the full transcript through canonical capture semantics and defaults `flush_policy` to `end_only`; legacy `flush` is accepted as an alias.

### `recall`

Single public grounded read verb. Use:

- `effort="low"` for quick lookup
- `effort="medium"` for default grounded recall
- `effort="high"` for deeper temporal/causal/audit recall

Do not use `budget`; it is not a public MCP API field. `effort="dynamic"` is reserved and currently rejected.

### `ingest`

Imports a local transcript file readable by the server process. Supported v1 parser formats:

- `json`
- `jsonl`
- `markdown` / `text`
- `auto`

Ingest normalizes transcript turns and routes them through `capture`; it does not write `.beads`, `.turns`, indexes, claims, or associations directly.

### `status`

Read-only store/server status: root, counts, adapters, MCP version, and server version.

## Prompt

The server exposes prompt:

```text
core-memory.agent-guide
```

The prompt content is packaged with Core Memory from `core_memory/integrations/mcp/core-memory-agent-guide.md`. The docs copy at `docs/agent-guide/core-memory-agent-guide.md` is a pointer, not a separate runtime source of truth.

## Remove client config

```bash
core-memory mcp uninstall --client cursor
```
