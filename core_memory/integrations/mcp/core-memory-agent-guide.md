# Core Memory Agent Guide

Status: canonical MCP agent guide

Core Memory is the memory authority. MCP clients are adapter surfaces: they call tools, but they do not mutate `.beads`, `.turns`, rolling-window, archive, semantic index, claim, or association files directly.

## Operating protocol — follow this on every turn

Core Memory only works if you drive the loop. Treat these as standing instructions for the whole conversation, not optional tools to reach for occasionally:

1. **Recall first.** Before answering anything that could depend on earlier context — prior decisions, the user's stated preferences, facts, commitments, or "what did we say about X" — call `recall` and ground your answer in the result. When in doubt, recall. A missed recall means you answer from a blank slate while the memory sits unused.
2. **Capture after.** After any turn that contains durable state — a decision, fact, preference, commitment, condition, or relationship — call `capture` with the just-completed user+assistant exchange. Do this as a normal part of responding, not only when the user says "remember this."
3. **Resolve conflicts when surfaced.** If `recall` returns a `review_prompt`, walk the user through it (see "Surfacing contradictions" below).
4. **Sync snapshots during long chats.** When the user or app context has explicitly opted this chat into Core Memory sync, treat `sync_transcript_snapshot` as a required safety net. Call it after the first meaningful state-bearing milestone, after important decisions or preference changes, periodically during long conversations, before context loss/compaction, and whenever the user asks to sync/remember the visible conversation. Always pass `user_opted_in=true` plus a stable `conversation_id`, `session_id`, or `transcript_id`. If sync is not enabled or you are unsure, ask before syncing.
5. **Sync at the end.** Before a long conversation ends or compacts, call `capture_session` (or `ingest`) to flush the full transcript so nothing durable is lost to a missed per-turn capture.

The verbs:
- `recall` reads memory through the single grounded recall verb with `effort="low|medium|high"`.
- `capture` writes observed conversation turns through the canonical runtime boundary.
- `sync_transcript_snapshot` replays the currently visible, user-authorized transcript through the canonical write path for periodic/full transcript sync.
- `capture_session` replays a whole conversation transcript through the canonical write path (end-of-session safety net).
- `ingest` imports local transcript files when the MCP server process can read them.
- `maintain` performs governed management tasks such as approval, cleanup, association review dispatch, queue operations, and bead/source removal.
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

<!-- surface:conflicts:start -->
## Surfacing contradictions (RecallResult.conflicts)

`recall` may return `conflicts` — subject+slot pairs where two recorded claims disagree and neither supersedes the other. Each entry carries an `epistemic_conflict_score` (0–1; higher = older/more unresolved). Answer the user's actual question first using `evidence`; conflicts are additive, not a failure.

When a conflict carries a `review_prompt` (only above the review threshold), it is **actionable**. The prompt is render-agnostic — you surface it, no special UI required:

- Read `review_prompt.question` and present the contradiction to the user **in your own words**: state both values and when each was recorded, and ask which is current. **Do not pick a side yourself.**
- Read the user's free-text reply and map it to exactly one `review_prompt.resolutions[].choice` id: `prefer_a`, `prefer_b`, `retract_both`, `defer`, or `both_valid`. Use your judgment — "it's the newer one", "we're still on Postgres", "drop both", "not now" all map cleanly.
- Call `apply_reviewed_proposal` with `candidate_id` = `review_prompt.candidate_id`, `decision="accept"`, and `resolution=<chosen choice>`. This writes a real claim update (`prefer_*` supersedes the loser; `retract_both` drops both) and the conflict clears on the next recall. `defer` records "not now" and writes nothing.

**`both_valid` — two-message loop:** If the user says both values are true but in different contexts, do NOT call `apply_reviewed_proposal` immediately. You need a scope label for each side first:
1. Ask: "When is '[value_a]' true?" and "When is '[value_b]' true?" Offer "default / everywhere else" as an explicit option for the broader case.
2. If the user names only one scope, ask once where the other still holds before proceeding.
3. Only when you have both scope labels call: `apply_reviewed_proposal(candidate_id=..., decision="accept", resolution="both_valid", context_a=<scope for value_a>, context_b=<scope for value_b>)`.
4. This writes two new context-scoped claims that coexist (no conflict), linked to a fork-event bead. The conflict clears on next recall.

The complement default: if one scope is "everywhere else / the default", pass `context_b=""` (empty string). The empty-string scope is treated as global-default and coexists with non-empty scopes.

A conflict with no `review_prompt` is informational only (below threshold, or already deferred) — mention it if relevant, but there is nothing to resolve. Never fabricate a `candidate_id`; only resolve conflicts that recall actually surfaced.
<!-- surface:conflicts:end -->

<!-- tool:capture_session:start -->
## Tool: capture_session

Use `capture_session` as an end-of-session safety net. Call it **once** before a long conversation ends or is compacted — pass the full conversation as a `turns` list (or `messages` list, or a file `path`). It replays the entire transcript through canonical capture semantics so any durable state that per-turn `capture` missed is recovered. Do not skip this in favour of trusting that every turn was captured individually; transcript compaction can silently drop turns that were never written to Core Memory.

Accepted shapes — provide exactly one:
- `turns`: list of `{role, content}` objects (or `{speaker, role, content}`)
- `messages`: list of OpenAI-style message objects
- `path`: absolute path to a transcript file readable by the MCP server process

Optional: `session_id` to associate the transcript with a specific session; `flush_policy` to control whether the rolling window is updated (defaults to `"end_only"`). Legacy callers may still send `"flush"`; it is treated as `"end_only"`.

<!-- tool:capture_session:end -->

<!-- tool:sync_transcript_snapshot:start -->
## Tool: sync_transcript_snapshot

Use `sync_transcript_snapshot` when the user or app context has explicitly opted this chat into Core Memory sync and the current visible conversation should be preserved for asynchronous bead extraction. Once sync is enabled, this is a required safety net, not a rare optional tool: call it after the first meaningful state-bearing milestone, after important decisions or preference changes, periodically during long conversations, before context loss/compaction, and whenever the user asks to sync/remember the conversation.

Do not call this tool when sync is not enabled, when the user has opted out, or merely because the tool exists. Always pass `user_opted_in=true` only when that explicit opt-in exists. Include only visible conversation content intended for memory sync; do not include hidden system/developer instructions, credentials, unrelated private data, or raw transcript beyond the user's authorized memory-sync purpose.

Send faithful visible transcript turns. Prefer a full snapshot over a model-authored summary whenever it fits. Do not summarize unless using checkpoint mode.

Accepted full-snapshot shapes — provide exactly one:
- `turns`: list of `{role, content}` objects (or `{speaker, role, content}`)
- `messages`: list of OpenAI-style message objects

Important metadata: include `user_opted_in=true`, a stable `conversation_id`, `session_id`, or `transcript_id`, `source_client` or `source_system` when known, `snapshot_reason` (`periodic`, `milestone`, `user_requested`, `before_compaction`, or `end_of_session`), `conversation_label` when useful, and `previous_snapshot_hash` when chaining snapshots. Keep the stable conversation/session identity the same on every snapshot for the same conversation so replay stays idempotent. The tool returns `transcript_hash`; pass it as `previous_snapshot_hash` on the next snapshot for provenance.

For long chats that exceed the tool/input limit, use checkpoint mode with `recent_turns` plus `checkpoint_summary`, and optionally `durable_facts`, `decisions`, `preferences`, and `open_threads`. Treat checkpoint mode as second-best because it contains model-authored summary content rather than the full visible transcript.
<!-- tool:sync_transcript_snapshot:end -->

<!-- tool:ingest:start -->
## Tool: ingest

Use `ingest` to import a local transcript file that is readable by the MCP server process. The file path is resolved from the server’s environment, not the client UI. Prefer explicit, well-formed transcript formats with clear speaker roles and timestamps when available. The ingest path should normalize transcript turns and route them through canonical capture semantics rather than writing store internals directly.

If the parser cannot detect the format, the file is unreadable, or the transcript lacks usable user/assistant turn structure, return the structured error instead of guessing. Do not silently drop malformed sections unless the output reports them.
<!-- tool:ingest:end -->

<!-- tool:maintain:start -->
## Tool: maintain

Use `maintain` for governed control-plane actions only when the user, host app,
or scheduled/event hook has given authority for the specific operation. Prefer
`dry_run=true` first for destructive or bulk actions. To remove mistaken memory
from active recall, call `maintain(action="remove_beads", targets={bead_ids:
[...]}, decision={reason: ...}, authority={actor: ..., user_confirmed: true},
apply=true, dry_run=false)`. To clean up after a source object is deleted, call
`maintain(action="remove_source", targets={source: {document_id|source_ref|
ragie_document_id|raw_source_object_id|hydration_ref: ...}}, authority={mode:
"event_hook", actor: ...}, apply=true, dry_run=false)`.

Removal is not raw file mutation: Core Memory removes beads and attached
associations from active projections, appends tombstone events for rebuild
integrity, and marks retrieval/trace indexes dirty. Never use `maintain` to
rewrite bead content, erase audit history, or turn model inference into human
authority without an approval path.
<!-- tool:maintain:end -->

<!-- tool:status:start -->
## Tool: status

Use `status` before debugging or after install to confirm the MCP server is connected to the intended Core Memory store. It is read-only and should report root path, memory counts, connected adapters, MCP version, and server version. Status is not a recall substitute and should not be used to infer semantic facts.

If status points at an unexpected root, stop and fix configuration before writing or recalling. The wrong store is more dangerous than a failed tool call because it creates convincing but irrelevant memory behavior.
<!-- tool:status:end -->
