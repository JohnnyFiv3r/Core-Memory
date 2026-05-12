# Core Memory Agent Guide

Status: canonical MCP agent guide

Core Memory is the memory authority. MCP clients are adapter surfaces: they call tools, but they do not mutate `.beads`, `.turns`, rolling-window, archive, semantic index, claim, or association files directly.

Use the public verbs deliberately:
- `capture` writes observed conversation turns through the canonical runtime boundary.
- `recall` reads memory through the single grounded recall verb with `effort="low|medium|high"`.
- `ingest` imports local transcript files when the MCP server process can read them.
- `status` checks whether the server and store are alive.

Prefer grounded partial answers over false certainty. If recall lacks credible evidence, say so. Do not invent memories, claims, edges, or sources.

<!-- tool:capture:start -->
## Tool: capture

Use `capture` only for completed, observed conversation turns that should enter Core Memory through the canonical write boundary. Provide either a structured `turns` list or the shortcut `{user, assistant, as_user?, as_assistant?}`. Do not provide both shapes in one call. The runtime owns ids, timestamps, idempotency, persistence, semantic indexing triggers, promotion, and replay safety; the client’s job is to provide faithful turn content.

Capture durable facts, decisions, preferences, commitments, conditions, relationships, and other state-bearing turns. Do not use `capture` for speculative notes, retrieval questions, generic acknowledgements, or fabricated summaries. If a turn is thin, it is acceptable for the resulting memory to be thin; do not pad it with vague semantic prose just to make it look richer.
<!-- tool:capture:end -->

<!-- tool:recall:start -->
## Tool: recall

Use `recall` whenever the user asks what Core Memory knows, why a decision was made, what changed over time, or which remembered evidence supports an answer. `recall` is the single public read verb; choose `effort` rather than selecting internal retrieval primitives. Use `effort="low"` for fast direct lookup, `effort="medium"` for default grounded recall, and `effort="high"` for deeper multi-hop, temporal, audit, or benchmark-grade recall.

Return answers grounded in the `RecallResult`: evidence, sources, tier path, steps, warnings, and planning metadata. If the result is empty or weak, abstain or answer narrowly. Do not claim unsupported certainty, do not hide conflicts, and do not treat the user’s retrieval question as a new durable memory.
<!-- tool:recall:end -->

<!-- tool:ingest:start -->
## Tool: ingest

Use `ingest` to import a local transcript file that is readable by the MCP server process. The file path is resolved from the server’s environment, not the client UI. Prefer explicit, well-formed transcript formats with clear speaker roles and timestamps when available. The ingest path should normalize transcript turns and route them through canonical capture semantics rather than writing store internals directly.

If the parser cannot detect the format, the file is unreadable, or the transcript lacks usable user/assistant turn structure, return the structured error instead of guessing. Do not silently drop malformed sections unless the output reports them.
<!-- tool:ingest:end -->

<!-- tool:status:start -->
## Tool: status

Use `status` before debugging or after install to confirm the MCP server is connected to the intended Core Memory store. It is read-only and should report root path, memory counts, connected adapters, MCP version, and server version. Status is not a recall substitute and should not be used to infer semantic facts.

If status points at an unexpected root, stop and fix configuration before writing or recalling. The wrong store is more dangerous than a failed tool call because it creates convincing but irrelevant memory behavior.
<!-- tool:status:end -->
