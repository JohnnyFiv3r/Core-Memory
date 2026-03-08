# Truth Hierarchy Policy

Status: Canonical (Phase T5)
Purpose: deterministic conflict resolution policy across memory surfaces.

## Conflict resolution rules

### Rule 1: Immediate verbatim queries
If user asks about very recent exact wording:
- prefer **Transcript**
- use Session Beads as secondary support

### Rule 2: Durable cross-session memory queries
If user asks remember/why/when/what changed about prior work:
- prefer **Archive Graph** and structured retrieval paths
- use Session Beads only as supporting context

### Rule 3: Continuity artifact usage
Rolling Window is a continuity layer, not a primary truth source.
- use for continuity guidance
- do not treat as canonical specificity source when archive evidence exists

### Rule 4: OpenClaw semantic memory
`MEMORY.md` remains parallel and complementary.
- useful for OpenClaw semantic memory ergonomics
- does not override canonical structured archive/bead truth

### Rule 5: Explicit disagreement handling
If transcript and durable memory disagree:
- for immediate exact wording -> transcript wins
- for durable project history -> archive graph wins
- surface disagreement explicitly when material
