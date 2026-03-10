# V2 Surface Authority Matrix

Status: Canonical (V2-P1)
Purpose: explicitly define responsibilities and authority of each surface.

| Surface | Lifecycle | Authority | Not authoritative for | Write path |
|---|---|---|---|---|
| Transcript | active session | exact immediate wording | durable structured memory | chat/runtime transcript pipeline |
| Session memory file | active session until flush | full-fidelity session beads + per-turn enrichment state | cross-session durable retrieval as final source | append-only per turn |
| Rolling window | updated at flush | continuity injection for new session start | deep/full-fidelity history | flush-stage projection write |
| Archive graph/store | updated at flush | durable retrieval and full context source | immediate verbatim transcript truth | flush-stage archive write |
| MEMORY.md (OpenClaw) | parallel semantic memory | OpenClaw semantic summaries | Core Memory runtime/storage truth | OpenClaw memory subsystem only |

## Runtime priority (agent interpretation)
1. active session (transcript + same-session beads)
2. rolling window injected context
3. archive retrieval via tools (`memory.execute` / `memory.search` / `memory.reason`)

## Boundary rules
- Core Memory does not interact with `MEMORY.md`.
- Archive is not compaction output; archive is durable full-fidelity DB.
- Rolling window compression does not alter archive fidelity.
