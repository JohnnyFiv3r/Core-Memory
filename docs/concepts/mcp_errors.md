# MCP Error Codes

Core Memory MCP tools return stable operational error codes. Protocol-level
errors (malformed MCP messages, tool not found, unsupported handshake shape) are
handled by the MCP SDK; Core Memory domain failures use the `cm.*` namespace.

| Code | Meaning | Typical fix |
|---|---|---|
| `cm.store_not_found` | Configured store path is missing or unreadable. | Check `--root`, `CORE_MEMORY_ROOT`, service environment, and file permissions. |
| `cm.invalid_turn` | `capture` input failed turn schema validation. | Provide non-empty speaker/content fields or use the `{user, assistant}` shortcut. |
| `cm.parser_format_unsupported` | `ingest` could not detect or support the file format. | Provide a supported transcript format or an explicit parser hint when supported. |
| `cm.parser_aborted` | `ingest` parsing failed mid-file. | Fix malformed turns, missing user/assistant pairing, or invalid timestamps. |
| `cm.path_not_readable` | `ingest.path` is not readable by the MCP server process. | Use an absolute local path and verify service user permissions. |
| `cm.recall_effort_exhausted` | `recall` exhausted the selected `effort` limit before a complete answer. | Retry with `effort="high"` or narrow the query. |
| `cm.recall_ungrounded` | `recall` could not produce a grounded answer. | Add/capture supporting memory or ask a narrower query. |
| `cm.unsupported_mcp_version` | Client negotiated an MCP spec version Core Memory does not support. | Upgrade Core Memory or configure a compatible MCP client version. |

Error code names are public surface. New codes may be added, but existing codes
should not be renamed or repurposed without a deprecation cycle.
