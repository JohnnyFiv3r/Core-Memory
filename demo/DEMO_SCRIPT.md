# Core Memory Live Demo Script

## Setup

```bash
rm -rf demo/memory_store   # clean slate
python demo/app.py
```

Open http://127.0.0.1:8080

You'll see: chat panel (left), memory inspector (right), context budget bar (top).
Beads tab should be empty. No pre-loaded data.

---

## Act 1: Cold Start — No Memory

**Type:** `What database are we using and why?`

**Point out:**
- **Chat**: Agent gives a generic answer — it has no project context, no memory to draw from
- **Beads tab**: A bead appeared. Click it — classified as `context` (it's a question, not a declaration)
- **Budget bar**: Started tracking tokens

**Say:** "The agent has no memory. It can only give generic advice. Now let's give it something to remember."

---

## Act 2: First Decision — Watch a Bead Get Written

**Type:** `We chose PostgreSQL over MySQL and SQLite. JSONB support and 2x better performance on our JSON workload made it the clear winner.`

**Point out:**
- **Beads tab**: A new bead appeared. Click it to show:
  - `type` — engine auto-classified this as a `decision`
  - `source_turn_ids: ["t-002"]` — proves this came from turn 2
  - `summary` — extracted key points from what you typed
  - `status: "promoted"` — the decision pass recognized this has causal reasoning and promoted it immediately
- Compare to the Act 1 bead — that one is `context` and `candidate`, this one is `decision` and `promoted`. Different type, different lifecycle.

**Say:** "That single message wrote a structured memory bead — typed, timestamped, with provenance. It was immediately promoted because the engine recognized it as a decision with supporting reasoning. Not every bead earns that — questions and context stay as candidates."

---

## Act 3: Second Turn — Watch Associations Form

**Type:** `We learned that synthetic benchmarks can be misleading. Always test with representative workloads — that's how we caught the 2x gap.`

**Point out:**
- **Beads tab**: New bead appeared. Click it — classified as a `lesson`
- Click the bead to expand it — show `association_preview` in the JSON. It found candidate links to the PostgreSQL decision bead but they're `authoritative: false`
- **Associations tab**: Still empty — by design. Associations are queued per-turn and committed at flush.

**Say:** "The engine crawled the session window and found candidate links to the decision from turn 2. These are queued — they get committed when the session flushes. This prevents half-formed associations from polluting the graph mid-conversation."

---

## Act 4: Add a Goal

**Type:** `We need to migrate authentication to OAuth2 by end of Q2. Legal flagged our session token storage as non-compliant.`

**Point out:**
- **Beads tab**: New bead — classified as a `goal`
- Click it — more candidate associations queuing in `association_preview`
- **Budget bar**: Climbing — four turns of context consumed

**Say:** "Four turns, four typed beads — a context, a decision, a lesson, a goal. Each auto-classified by the LLM, with candidate associations queued for flush."

---

## Act 5: Prove Memory Works — Ask a Question

**Type:** `Why did we choose PostgreSQL?`

**Point out:**
- **Chat**: Agent answers specifically — cites JSONB, the benchmark, the lesson. Compare this to Act 1's generic answer.
- **Beads tab**: Fifth bead created, classified as `context` (it's a question)
- Compare the agent's answer to the actual bead content by clicking the PostgreSQL decision bead

**Say:** "Compare this to Act 1. Same kind of question, completely different answer. The agent isn't hallucinating — it searched memory, found the decision bead, and grounded its answer in what we actually said."

---

## Act 6: Session Flush — The Key Moment

**Click:** `Flush Session` button

**Point out:**
- **Chat**: System message confirms the flush
- **Session badge**: Session ID changed — completely new session
- **Associations tab**: NOW populated — queued associations committed to the graph
- **Rolling Window tab**: NOW populated — shows the beads that will carry over
- **Beads tab**: Decision and lesson were already `promoted` per-turn (the engine promotes eagerly when it sees strong reasoning). Goal stays `candidate` (goals await resolution, they don't promote like decisions).
- **Budget bar**: Reset to 0%

**Say:** "This is the session boundary. The queued associations are now committed to the graph. Full beads are archived, unpromoted ones compressed, and the rolling window rebuilt — a FIFO context buffer within a token budget. In production this fires automatically at 80% context capacity."

**If asked about goals:** "Goals have a different lifecycle. They stay as candidates until a later turn marks them as resolved — for example, someone saying 'we finished the OAuth2 migration' would create an outcome bead linked to the goal. That resolution mechanism is on the roadmap."

---

## Act 7: Memory Survives the Reset

**Type:** `What decisions has the team made?`

**Point out:**
- **Chat**: Agent answers from memory — knows about PostgreSQL, the benchmark lesson, the OAuth2 goal. All from the rolling window injection.
- **Rolling Window tab**: This is what the agent received at session start
- The agent has zero message history from the previous session

**Say:** "Context window was fully reset. No message history. But the agent knows everything — the rolling window was injected as a system prompt. This is how you get persistent memory without infinite context."

---

## Act 8: The Proof — Trace It Back

**Type:** `Why did we decide to always benchmark before choosing infrastructure?`

**Point out:**
- **Chat**: Agent traces the lesson back to the PostgreSQL decision
- **Beads tab**: Click the lesson bead — show `source_turn_ids: ["t-003"]` (turn 3 created it)
- Click the decision bead — show `source_turn_ids: ["t-002"]` (turn 2)
- Show the association linking them
- Note: this bead may classify as `precedent` instead of `context` — the classifier sometimes interprets "did we decide" as referencing a past pattern. This is a known area for improvement in the type classifier.

**Say:** "Click any bead — see exactly when it was created, which turn produced it, and the causal chain. Deterministic, inspectable, auditable. Not a black box."

---

## Closing Points

- Every turn writes a typed, structured bead — not raw text
- Associations form automatically between related beads
- Session flush compresses and builds a rolling context window
- Memory survives context resets without infinite context
- Everything is inspectable — click any bead, trace the provenance
- Pure Python, zero runtime dependencies, file-based storage
- Works with any LLM provider (Anthropic, OpenAI, etc.)

---

## Troubleshooting

```bash
# Port in use
lsof -ti:8080 | xargs kill -9

# Clean slate
rm -rf demo/memory_store

# Check API key
cat .env
```
