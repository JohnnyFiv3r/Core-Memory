# PRD 03: MCP protocol server

**Status:** Locked
**Owner:** John Inniger
**Reviewer:** Chris
**Last updated:** 2026-05-11
**Related TODO:** [Core-Memory-Demo TODO #1](https://github.com/JohnnyFiv3r/Core-Memory-Demo/blob/main/TODO.md) (originating tracking item)

## 1. Summary

This PRD ships an MCP streamable-HTTP protocol server at `/mcp`, mounted alongside the existing FastAPI app in `core_memory/integrations/http/server.py`. The server exposes a tightly-scoped set of Core Memory tools (mirroring the public CLI verbs: `capture`, `recall`, `ingest`, `status`) so that any MCP-speaking client — Claude Code, Cursor, Windsurf, Open WebUI — can install Core Memory with one config snippet. The PRD also ships a one-command install path (`core-memory mcp install --client <name>`) that mirrors the existing OpenClaw plugin-install ergonomics: detect the client's config location, patch it, start the server, verify the connection.

Core Memory keeps the MCP-typed operations it already exposes at `/v1/mcp/*` as plain REST. Both surfaces live side by side — REST for callers that don't speak MCP, streamable-HTTP for clients that do.

## 2. Background & motivation

Core Memory has MCP-typed operations (`query_current_state`, `query_temporal_window`, `query_causal_chain`, `query_contradictions`, `write_turn_finalized`, etc.) exposed today as plain REST under `/v1/mcp/*`. The *logic* is in place; the *transport* isn't. Clients that natively speak MCP — Claude Code, Cursor, Windsurf, Open WebUI — can't wire Core Memory up with the standard MCP config block. They'd need custom glue: a REST adapter, request shaping, response unwrapping. Almost nobody does that work; they reach for mem0 or mnemory, which ship as proper MCP streamable-HTTP servers.

Meanwhile, the agent-runtime user base is growing fast. Claude Code is the daily driver for a meaningful slice of the developer community, and Cursor / Windsurf / Open WebUI have parallel adoption curves. For these users, the "install a memory layer" experience is governed by what their client supports, and their client supports MCP. Core Memory has the better underlying architecture (causal recall, multi-hop chains, the agentic orchestrator from PRD #5) — but the transport gap means they never see it.

Adding `/mcp` is mostly wiring. The MCP-typed tool functions already exist; this PRD lifts them onto a protocol-compliant transport, picks a deliberate v1 tool surface, and ships an install command that makes the integration a one-command experience instead of a JSON-paste-and-pray.

## 3. Goals / Non-goals

### Goals

- **Install Core Memory into Claude Code / Cursor / Windsurf / Open WebUI in under 30 seconds** via a one-command install (`core-memory mcp install`) that mirrors the OpenClaw plugin-install ergonomics — detect the client, patch its config, start the server, verify.
- **Achieve parity with mem0 / mnemory** on first-touch MCP integration. An adopter comparing memory layers should not see "no native MCP transport" as a Core Memory limitation.
- **Bridge to the differentiated recall path.** Once PRD #5 (single-verb recall orchestrator at `POST /api/recall`) ships, the MCP server exposes it as the `recall` tool — adopters get causal traversal and multi-hop reasoning through a standard transport, not a Core-Memory-only REST quirk.
- **Mirror the public CLI** as the tool surface. `capture`, `recall`, `ingest`, `status` map 1:1 from CLI verb to MCP tool. Adopters who know the CLI know the MCP surface.
- **Preserve the existing `/v1/mcp/*` REST endpoints unchanged.** No migration required for current REST callers; both surfaces ship side by side.

### Non-goals

- **Stdio-based MCP transport.** Streamable-HTTP only for v1. Stdio is a different transport with different lifecycle semantics; deferred.
- **Hosted MCP server / cloud deployment.** Core Memory's MCP server ships as a library; adopters self-host (or hit the Core-Memory-Demo deployment, which is its own deployment story). Hosting becomes a paid-tier question if/when monetization arrives; out of scope here.
- **Custom MCP protocol extensions.** We expose tools through standard MCP; no Core-Memory-only protocol additions.
- **MCP-specific authentication beyond what the protocol provides.** If MCP gets auth, we use it; we don't invent our own.
- **Auto-discovery / dynamic plugin registration.** Captured as TODO #21 (plugin marketplace). The MCP server registers a fixed tool set in v1.
- **Future MCP tools.** Two tools are explicitly flagged for v2 (not v1):
  - `remember` (declarative user-authored writes) — depends on the typed-memory work in TODO #21.
  - `inspect` / `open_viewer` (WebUI graph viewer with bead inspection) — wraps the Core-Memory-Demo Reagraph view so an MCP client can launch a browser tab onto the user's memory graph. Real product opportunity, but not v1.
- **Breaking the existing `/v1/mcp/*` REST endpoints.** They stay. The MCP protocol server is *additional*.

## 4. User stories

### 4.1 Claude Code user wanting Core Memory *(primary product — biggest adoption surface)*

A developer running Claude Code wants persistent causal memory across their sessions. They install Core Memory with `pip install core-memory` and run:

```
core-memory mcp install --client claude-code
```

The command detects `~/.claude/`, patches the MCP servers section of the config with the right `core-memory` entry, starts the local MCP server as a backgrounded process (or registers a launchd/systemd unit), and verifies that Claude Code can connect. The user closes and reopens Claude Code; the `capture`, `recall`, `ingest`, and `status` tools are now available. Total time from `pip install` to working memory tools: under 30 seconds.

If they later want to remove it: `core-memory mcp uninstall --client claude-code` reverses everything.

### 4.2 Cursor / Windsurf / Open WebUI user *(primary product)*

Same shape as 4.1 with a different `--client` flag. The install command handles the per-client config-file differences (different paths, different schema shapes for MCP server entries). If the client isn't pre-supported, the user falls back to the manual JSON paste documented in the README — they copy the standard MCP config block, paste it into their client's config, and start the server manually with `python -m core_memory.integrations.http.server`.

### 4.3 MCP-client developer building tooling *(primary product)*

A developer building an MCP client (custom agent, IDE plugin, automation tool) needs Core Memory to behave like any other MCP server: enumerable tools, protocol-compliant request/response shapes, clear error semantics, version negotiation. They point their client at `http://localhost:8000/mcp`, get the standard MCP handshake, enumerate the four v1 tools, and call them by name. No Core-Memory-specific knowledge required beyond the tool docstrings.

### 4.4 Existing `/v1/mcp/*` REST API user *(primary product — don't break them)*

Someone already integrated against Core Memory's `/v1/mcp/*` REST endpoints — maybe an internal adapter, maybe a script, maybe a third-party integration. After PRD #3 ships, their integration continues to work unchanged. The REST endpoints are not deprecated; they live side by side with the MCP protocol server. The release notes call this out explicitly.

### 4.5 Internal Core Memory dev maintaining the MCP tool registry *(internal-only)*

The MCP tool registry is a single Python module that maps tool names to handler functions. When the CLI adds a new verb, the registry adds the matching MCP tool (or explicitly chooses not to, with a comment explaining why — e.g., why `init` and `connect` are CLI-only). The registry's structure makes "what's exposed via MCP today" answerable by reading one file.

## 5. Detailed design

### 5.1 Server architecture and mount point

The MCP server mounts at `/mcp` (root, no version prefix) inside the existing FastAPI app at [core_memory/integrations/http/server.py](../../core_memory/integrations/http/server.py). Concretely:

```python
# core_memory/integrations/http/server.py
from fastapi import FastAPI
from core_memory.integrations.mcp.protocol_server import build_mcp_app

app = FastAPI(...)
app.include_router(v1_router, prefix="/v1")          # existing REST API
app.mount("/mcp", build_mcp_app())                   # new MCP streamable-HTTP server
```

The MCP protocol server is a separate sub-app (FastAPI `app.mount(...)`). This keeps MCP isolated from REST so it can be swapped, version-bumped, or run standalone without touching `/v1/*`. The two surfaces are peers.

`/mcp` lives at the root because MCP has its own protocol versioning (negotiated during the protocol handshake), not Core Memory's REST API versioning. Clients expect `/mcp`, not `/v1/mcp`.

### 5.2 MCP spec version pinning

Pin to the spec version Claude Code currently speaks (read from Anthropic's MCP docs at PRD-author time and from Claude Code's release notes). Track new spec releases as they land in Claude Code; aim to upgrade Core Memory's pin within ~1 release cycle of Claude Code adopting a new spec.

- Pinned version recorded in `pyproject.toml` (as a dependency version on the MCP SDK) and surfaced in the README's MCP install section.
- The streamable-HTTP server returns a clear "unsupported MCP version" error if a client speaks a version we don't support.
- A `core-memory mcp version` CLI subcommand prints the pinned spec version + the supported MCP SDK version (operational visibility for adopters debugging client/server mismatches).

The other major clients (Cursor, Windsurf, Open WebUI) tend to follow Claude Code's MCP version trajectory within weeks; pinning to Claude Code's version maximizes the "works for the most users" outcome without making this PRD's owner track three spec releases simultaneously.

### 5.3 Tool registry and handler pattern

Single Python module at `core_memory/integrations/mcp/registry.py`. Maps tool names to wrapper functions. Each wrapper:

1. Receives the MCP request payload.
2. Adapts MCP request shape → existing typed-write/typed-read function signature.
3. Calls the typed function.
4. Adapts the function's response back to MCP shape.
5. Catches Core Memory errors and normalizes them per §5.6.

```python
# core_memory/integrations/mcp/registry.py
from typing import Callable

TOOLS: dict[str, Callable] = {
    "capture":  capture_wrapper,
    "recall":   recall_wrapper,
    "ingest":   ingest_wrapper,
    "status":   status_wrapper,
}
```

The registry is a single file. When a new tool is added (e.g., post-v1 `remember` or `inspect`), it's a one-line registry entry plus one wrapper function. The registry's structure makes "what's exposed via MCP today" answerable by reading one file (per §4.5).

Wrappers are explicit (not auto-generated from typed function annotations) because the MCP request/response shape and the Core Memory typed signatures will drift over time, and wrappers absorb that drift without coupling the typed functions to MCP framework decorators.

### 5.4 Tool definitions (v1 surface)

Four tools, mirroring the public CLI verbs. Each tool's MCP schema (input/output JSON Schema) lives next to its wrapper in `core_memory/integrations/mcp/tools/`.

#### `capture`
- **Input:** `{turns: list[Turn]}` (Turn per PRD #1) OR the 2-speaker shortcut `{user: str, assistant: str, as_user?: str, as_assistant?: str}`.
- **Output:** `{ok: true, session_id: str, turn_id: str, bead_ids: list[str]}` on success.
- **Behavior:** Canonical write boundary. Delegates to `Memory.capture` → `process_turn_finalized`.
- **Errors:** `cm.invalid_turn`, `cm.store_not_found`.

#### `recall`
- **Input:** `{query: str, effort?: "low"|"medium"|"high", speaker?: str|list}`.
- **Output:** The shared `RecallResult` shape from Core-Memory-Demo TODO #4 / Core-Memory main TODO #7 — `{answer, why, evidence, sources, tier_path, steps}`.
- **Behavior:** Single recall verb with internal three-tier scaling. Delegates to the recall orchestrator from PRD #5 using public effort naming (`low` = fast direct lookup, `medium` = default grounded recall, `high` = deeper multi-hop / temporal / benchmark-grade recall). Falls back to `memory_execute` with `intent="remember"` until then.
- **Errors:** `cm.recall_effort_exhausted`, `cm.recall_ungrounded`, `cm.store_not_found`.

#### `ingest`
- **Input:** `{path: str, from?: str, dry_run?: bool, session_prefix?: str, self_id?: str}`.
- **Output:** `{ok: true, import_id: str, turns_parsed: int, turns_written: int, errors: list[{turn_index, reason}]}`.
- **Behavior:** Transcript ingest. Delegates to the parser layer (Core-Memory main TODO #11) + `m.capture(turns)`. Path must be readable from the MCP server's process — typically a local file path on the user's machine.
- **Errors:** `cm.parser_format_unsupported`, `cm.parser_aborted`, `cm.path_not_readable`.

#### `status`
- **Input:** `{}` (no parameters).
- **Output:** `{ok: true, root: str, beads_total: int, sessions_total: int, last_capture_at: str|null, connected_adapters: list[str], mcp_version: str, server_version: str}`.
- **Behavior:** Read-only introspection. Lets MCP clients (and the humans behind them) check that Core Memory is alive, connected to the right store, and recently active.
- **Errors:** `cm.store_not_found`.

#### NOT in v1
- `init` — store should already exist; MCP isn't a setup tool. Setup happens via `core-memory mcp install`.
- `connect` — MCP IS the adapter from the client's perspective; nothing to "connect" through MCP.
- `remember` (declarative write) — depends on TODO #21 typed-memory work; v2.
- `inspect` / `open_viewer` (WebUI graph viewer) — v2, requires Core-Memory-Demo Reagraph view as a library.

### 5.5 Install command (`core-memory mcp install`)

The install command mirrors the OpenClaw plugin-install ergonomics. Single command, plug-and-play for low-acuity users, escape hatches for power users.

#### Surface

```bash
core-memory mcp install [--client <name>] [--root <path>] [--port <n>] [--no-start]
core-memory mcp status                                  # is the server running? which clients are configured?
core-memory mcp uninstall [--client <name>]             # clean removal
core-memory mcp version                                 # pinned MCP spec version + SDK version (§5.2)
```

`--client` accepts: `claude-code`, `cursor`, `windsurf`, `open-webui`. With no `--client`, the command auto-detects all installed clients and installs into each found. With no installed client found, prints a helpful error pointing at the manual JSON-paste fallback in the README.

#### Install steps (per `--client`)

1. **Detect client config location.** Per-client paths (`~/.claude/`, `~/.cursor/`, `~/.config/Windsurf/`, etc.). Fail clearly if absent — point at the README's manual install fallback.
2. **Read existing config, parse JSON.** Preserve all unrelated keys.
3. **Add or update the `mcpServers.core-memory` entry** with the right URL (`http://localhost:<port>/mcp`).
4. **Write config back atomically** (temp file + `os.rename`).
5. **Start the MCP server** (unless `--no-start`):
   - **macOS:** Write a launchd plist to `~/Library/LaunchAgents/dev.linelead.core-memory-mcp.plist` and load it via `launchctl load`. Survives reboot.
   - **Linux:** Write a systemd user unit to `~/.config/systemd/user/core-memory-mcp.service`, then `systemctl --user enable --now core-memory-mcp`. Survives reboot.
   - **Windows:** v1 limitation — start as a foreground process; print a notice that survival requires manual Task Scheduler setup (or `--no-start` and run yourself). v2 adds Windows service integration.
6. **Verify.** HTTP GET on `http://localhost:<port>/mcp/healthz` returns 200 within 5 seconds. If not, surface the error and roll back the config change.
7. **Print confirmation** with next steps: "Installed. Restart Claude Code to pick up the new MCP server. Run `core-memory mcp status` to verify."

#### Defaults (low-acuity-user friendly)

- `--port`: `8000`. If port is in use, prompt to choose an alternative or pass `--port`.
- `--root`: `~/.core-memory/store/`. Auto-created on first install. Override via flag or `CORE_MEMORY_ROOT` env var (per §5.6).
- No env vars or config files required for the default path.

#### Power-user flags

- `--no-start`: install the config + service unit but don't start the server. For users who want manual control.
- `--port <n>`: override the default port (e.g., for users with 8000 conflicts).
- `--root <path>`: override the data store location at install time. Recorded in the service unit so subsequent reboots use the same path.

Implementation may include internal dry-run hooks for test safety, but dry-run is not part of the documented v1 user surface.

### 5.6 Data store discovery

When the MCP server starts, it needs to know which Core Memory store to attach to. Resolution order:

1. **`--root` flag from install command** (highest priority — baked into the service unit at install time).
2. **`CORE_MEMORY_ROOT` env var** (runtime override).
3. **`~/.core-memory/store/` default** (auto-created on first install).

The install command's `--root` flag writes the chosen path into the service unit (launchd plist `EnvironmentVariables` or systemd `Environment=`), so reboot survives the user's choice. No auto-detection from cwd — that's too magic and produces surprises in non-interactive contexts.

### 5.7 Error semantics

Two error layers, both surface as proper MCP error responses (not HTTP 500s with stack traces).

#### MCP protocol errors

Standard MCP responses for protocol-level issues — handshake failure, tool-not-found, malformed request, version negotiation failure. Whatever the MCP SDK emits by default; we don't customize.

#### Core Memory operational errors

Wrapped as MCP tool execution errors with a stable `error_code` and structured `data`. Internal Python exception types are NOT leaked to the client; they're normalized.

```json
{
  "error": {
    "code": "cm.invalid_turn",
    "message": "Turn.speaker must be non-empty.",
    "data": {
      "tool": "capture",
      "field": "turns[0].speaker",
      "received": ""
    }
  }
}
```

#### Stable error code namespace (v1)

| Code | Meaning |
|---|---|
| `cm.store_not_found` | The configured data store path is missing or unreadable. |
| `cm.invalid_turn` | The `capture` input failed schema validation (per PRD #1's §5.5). |
| `cm.parser_format_unsupported` | `ingest`: file format not detected and `--from` flag not provided. |
| `cm.parser_aborted` | `ingest`: parser failed mid-file (e.g., missing user-side or assistant-side turns per PRD #1's parser invariant). |
| `cm.path_not_readable` | `ingest`: the file at `path` is not readable from the MCP server process. |
| `cm.recall_effort_exhausted` | `recall`: orchestrator hit its step or token cap. Includes `best_partial` in `data`. |
| `cm.recall_ungrounded` | `recall`: the orchestrator returned no grounded answer; `data.reason` explains. |
| `cm.unsupported_mcp_version` | Client negotiated a spec version Core Memory doesn't support. |

Each error code is documented in `docs/concepts/mcp_errors.md` with a stable contract: code → meaning → typical fix. MCP clients can pattern-match on the code; humans get readable messages.

Error codes are part of the public surface — they don't change between releases without a deprecation cycle. New error codes can be added freely; existing codes don't get renamed or repurposed.

### 5.8 OpenClaw install survival upgrade (cross-reference, not in scope)

The launchd/systemd survival pattern from §5.5 is worth applying to the existing OpenClaw plugin install script (`scripts/openclaw_bridge_install.sh`) too — memory loss across reboots is a real adopter pain point regardless of which adapter is in use. Captured as TODO #27; not scoped to this PRD.

### 5.9 Agent instruction injection (hard requirement)

The MCP server MUST inject Core Memory's canonical agent instructions to connecting LLM clients via the protocol's native mechanisms. This is a non-negotiable design requirement: tools-without-instructions are guessed at, and Core Memory's value depends on the LLM client knowing how to use it correctly (when to capture vs. recall, what bead types mean, why recall is single-verb-with-internal-scaling).

The injection has two layers:

1. **Tool descriptions.** Each of the four v1 tools (`capture`, `recall`, `ingest`, `status`) carries a 100–300 word description extracted from the canonical agent guide. The MCP tool schema embeds these so clients see them at tool enumeration time.
2. **Server-exposed prompt.** The MCP server registers a `core-memory.agent-guide` prompt that returns the consolidated agent guide. Clients that surface MCP prompts to the LLM (Claude Code does) auto-deliver the full guide.

The canonical agent guide is owned by **TODO #7a** (consolidate scattered agent instructions into a single canonical source). The injection mechanism is owned by **TODO #7b** (build-time markdown-to-prompt loader + MCP registration). PRD #3 ships the MCP server scaffold; the agent-guide content and the loader land paired with this PRD.

**Dependency order:** #7a must land first (canonical doc exists), then #7b builds the loader and wires it into the MCP server. PRD #3's MCP server boots WITHOUT instruction injection until #7b lands (instructions degrade gracefully to minimal default tool descriptions), but the server is NOT considered shipped-for-real until #7a + #7b are complete.

Build-time tests (per #7b) catch silent drift between the canonical doc and the rendered prompt.

## 6. Migration & rollout

### 6.1 Release shape

PRD #3 ships in a single pinned release of `core-memory` (target: `1.2.0`). The release is **additive** — no breaking changes:

- New endpoint `/mcp` mounts inside the existing HTTP server.
- New CLI subcommand tree under `core-memory mcp ...`.
- New extras group `[mcp]` (per §6.2).
- Existing `/v1/mcp/*` REST endpoints are unchanged.
- Existing HTTP server callers see no behavior change.

Release notes lead with the install command and the user-facing UX ("`core-memory mcp install --client claude-code`"), not with the protocol endpoint. Adopters care about the integration outcome, not the wire format.

### 6.2 Dependencies and extras

The MCP SDK (Anthropic's reference implementation) is a new dependency. It ships under a new extras group to keep the base install lean:

```bash
pip install core-memory                # base install, no MCP server
pip install core-memory[mcp]           # MCP server included
pip install core-memory[http]          # existing REST HTTP server only, no MCP
pip install core-memory[mcp,http]      # both
```

`[http]` stays as-is for callers who want just the REST API without the MCP SDK install footprint. The MCP SDK pulls non-trivial transitive dependencies, so opting in matters.

### 6.3 Agent-instruction injection sequencing

The MCP server boots WITHOUT canonical-instruction injection if TODO #7a / #7b haven't landed yet. Tools carry their minimal default descriptions; the `core-memory.agent-guide` prompt is unregistered. This degrades gracefully — clients still get a working MCP integration, just without the rich instructions.

For PRD #3 to be considered shipped-for-real, both #7a (canonical agent guide) and #7b (build-time injection loader) must be merged. The PRD-#3 codebase ships first; the instruction injection lights up when #7b's loader is wired in.

### 6.4 Existing REST API

The `/v1/mcp/*` REST endpoints stay live. No deprecation, no migration timeline. They're a peer surface to the MCP protocol server. Adopters using them keep using them; new adopters likely prefer the MCP server but aren't forced.

### 6.5 OpenClaw install survival (cross-reference)

The launchd/systemd survival pattern from §5.5 lands as part of this PRD. TODO #27 applies the same pattern to the existing OpenClaw install script; it doesn't ship in the same release but uses the helpers built here. The shared `scripts/_service_helpers.sh` lands with PRD #3.

## 7. Acceptance criteria & test plan

### 7.1 Protocol-level acceptance
- The four v1 tools (`capture`, `recall`, `ingest`, `status`) are enumerable through the MCP protocol from a real Claude Code client. Tool schemas are valid per the pinned MCP spec version.
- Tool invocations round-trip successfully end-to-end: Claude Code calls `recall(query="why X")` → MCP server → Core Memory recall orchestrator → `RecallResult` → MCP response → Claude Code. Verified manually with a real Claude Code installation; unit-tested with a stub client.
- The `core-memory.agent-guide` prompt is registered and returns the rendered canonical agent guide (post-#7b).
- An MCP client speaking an older spec version receives a clean `cm.unsupported_mcp_version` error.

### 7.2 Install command acceptance
- `core-memory mcp install --client claude-code` works on macOS and Linux. Manual verification on both platforms.
- Install patches the client's config file without corrupting unrelated entries (test: install on a config with multiple existing MCP servers, verify all entries preserved).
- Install registers the launchd/systemd unit; the service starts; `core-memory mcp status` reports it running.
- Service survives reboot on macOS + Linux (manual test, since CI can't reboot).
- Windows install command succeeds with the documented foreground-process notice.
- `core-memory mcp uninstall --client claude-code` cleanly removes everything it installed: config entry, service unit, PID file, env-var registration. Test: install → uninstall → assert filesystem and config are byte-identical to pre-install.
- `--no-start`, `--port`, `--root` flags work as documented (unit tests).
- Auto-detect (`core-memory mcp install` with no `--client`) finds all installed clients and installs into each (mock filesystem test).

### 7.3 Error-code acceptance
- Every error code from §5.7 fires under the appropriate trigger (unit tests with mocked Core Memory state):
  - `cm.invalid_turn` on capture with empty speaker.
  - `cm.store_not_found` when the configured root doesn't exist.
  - `cm.parser_format_unsupported` on ingest with an unknown file shape.
  - `cm.parser_aborted` on ingest of a transcript missing user-side or assistant-side turns.
  - `cm.path_not_readable` on ingest of a file the server can't read.
  - `cm.recall_effort_exhausted` when the orchestrator hits its step/token cap.
  - `cm.recall_ungrounded` when the orchestrator can't ground its answer.
  - `cm.unsupported_mcp_version` on a version-mismatch handshake.
- Internal Python exception types do NOT leak to MCP clients (test: trigger each error path, assert the response is wrapped per §5.7).

### 7.4 Backward-compatibility acceptance
- Existing `/v1/mcp/*` REST endpoints continue to return identical responses under identical inputs (regression test against the existing REST test suite).
- Existing FastAPI app behavior is unchanged — same routes, same middlewares, same error handlers. The MCP mount is purely additive.

### 7.5 Done definition
The PRD is shipped when:
- All §7.1–§7.4 tests pass on master.
- TODO #7a (canonical agent guide) and #7b (instruction loader) are merged — without them, the MCP server boots but doesn't inject canonical guidance.
- Pinned release published to PyPI with `[mcp]` extras group.
- README quickstart documents the `core-memory mcp install` command alongside `pip install core-memory[mcp]`.
- An adopter installing fresh on a clean macOS or Linux machine can run a single `core-memory mcp install --client claude-code` and see Core Memory tools in their Claude Code session within 30 seconds (per §4.1).

## 8. Open questions

Decisions Chris's developer should escalate rather than guess. None block the technical plan; they shape it.

1. **Tool-description authoring source of truth.** Tool descriptions are 100–300 words each, embedded in the MCP tool schema. Where are they authored? Three options:
   - (a) In the wrapper function's docstring, extracted at registry-load time.
   - (b) As named sections in the canonical agent guide (#7a), extracted by the loader (#7b).
   - (c) As separate `.md` files under `docs/agent_guide/tools/` that the loader concatenates.
   **Lean (b):** single source of truth in the canonical doc, extracted programmatically. But (a) and (c) are defensible; raise for owner call.

2. **Background-process logging location.** §5.5 specifies launchd/systemd registration. Where do the MCP server's logs go? Common patterns vary:
   - **macOS:** Console.app via `os_log`, or `~/Library/Logs/core-memory/mcp.log`.
   - **Linux:** `journalctl` (systemd default), or `~/.local/share/core-memory/logs/mcp.log`.
   - **Cross-platform default:** `~/.core-memory/logs/mcp.log` (consistent location, easy to find).
   **Lean: cross-platform default** with a `--log-target` flag for OS-native logging. Confirm before building.

3. **MCP spec version drift handling.** Open: document the operational playbook when Claude Code adopts a new MCP spec version.

4. **Tool descriptions for inactive tools.** Resolved: always describe the target behavior using public `effort="low|medium|high"` naming; fallback behavior is an implementation detail until the orchestrator is active.

5. **Streamable-HTTP edge cases.** The transport has known quirks around long-running connections, retry semantics, and connection multiplexing. What's our position on:
   - Idle-connection timeout
   - Reconnection after client disconnect
   - Multiple simultaneous clients hitting the same server
   The MCP SDK should handle most of this; flag if defaults are surprising.

## 9. References

### Originating tracking
- [Core-Memory-Demo TODO #1](https://github.com/JohnnyFiv3r/Core-Memory-Demo/blob/main/TODO.md) — original capture of the gap

### Related TODO items (main Core-Memory)
- **#5** (recall orchestrator) — surfaced as the MCP `recall` tool.
- **#7** (shared CLI/demo output contract) — `RecallResult` shape returned by the MCP `recall` tool.
- **#7a** (consolidate agent instructions) — **blocks PRD #3's full release.**
- **#7b** (inject agent instructions into MCP) — **blocks PRD #3's full release.**
- **#11/#12** (transcript parser + ingest CLI) — surfaced as the MCP `ingest` tool.
- **#21** (plugin connectors) — future, not v1; explicitly out of scope per §3.
- **#27** (OpenClaw install survival upgrade) — uses the shared `scripts/_service_helpers.sh` built here.

### Agent-instruction sources (consolidation targets for #7a)
- [AGENT_INSTRUCTIONS.md](../../AGENT_INSTRUCTIONS.md) — top-level canonical contract
- [docs/canonical_contract.md](../../docs/canonical_contract.md)
- [docs/integrations/openclaw/core-memory-skill-instructions.md](../../docs/integrations/openclaw/core-memory-skill-instructions.md)
- [docs/specs/agent-authored-turn-memory-v1.md](../../docs/specs/agent-authored-turn-memory-v1.md)
- [docs/specs/agent-authored-rollout-playbook.md](../../docs/specs/agent-authored-rollout-playbook.md)

### Related contract docs
- [docs/concepts/turn_schema.md](../concepts/turn_schema.md) — `Turn` shape consumed by the `capture` tool.
- [docs/adapters/contract.md](../adapters/contract.md) — MCP is one adapter implementation under the broader adapter lifecycle contract.

### Code touch points
- [core_memory/integrations/http/server.py](../../core_memory/integrations/http/server.py) — MCP server mount.
- `core_memory/integrations/mcp/` — existing typed operations + new MCP protocol server + tool registry + agent-guide loader.
- [scripts/openclaw_bridge_install.sh](../../scripts/openclaw_bridge_install.sh) — install-path pattern that this PRD mirrors and that #27 upgrades.

### External
- MCP protocol spec — version pinned per §5.2; actual version recorded in `pyproject.toml` at ship time.
- Anthropic MCP reference SDK — pinned dependency per §5.2.
- Claude Code MCP config docs (for the auto-detect step in `core-memory mcp install`).
