# Portfolio Voice Agent v1 — Execution Beads

Date: 2026-02-22
Owner: Johnny + Krusty
Status: Ready for execution

## Milestone M1 — Realtime Voice Backbone

### B-001 — Repo Scaffold (TypeScript monorepo)
- Type: checkpoint
- Priority: P0
- Estimate: 0.5d
- Depends on: none
- Deliverables:
  - `portfolio-voice-agent/` root
  - `apps/web` (React/Vite)
  - `apps/voice-server` (Node/WS)
  - `packages/shared-types`
  - env templates + README runbook
- DoD: `pnpm dev` boots both web + server locally.

### B-002 — Shared WS Event Schema + Validation
- Type: decision
- Priority: P0
- Estimate: 0.5d
- Depends on: B-001
- Deliverables:
  - zod schemas for all client/server events
  - typed TS exports in `packages/shared-types`
- DoD: invalid payloads are rejected server-side with structured error.

### B-003 — Web Mic Capture + Session State Machine
- Type: tool_call
- Priority: P0
- Estimate: 1d
- Depends on: B-002
- Deliverables:
  - browser mic permission flow
  - state machine: idle/requesting_mic/gated/connecting/listening/thinking/speaking/error
  - mic toggle start/end
- DoD: state transitions visible in UI debug panel.

### B-004 — OpenAI Realtime Integration (audio in, text out)
- Type: evidence
- Priority: P0
- Estimate: 1d
- Depends on: B-003
- Deliverables:
  - WS relay from web -> voice-server -> OpenAI Realtime
  - partial + final transcript events
  - assistant text delta streaming
- DoD: user speech yields assistant text in under ~2s to first token.

### B-005 — ElevenLabs Streaming TTS Playback
- Type: outcome
- Priority: P0
- Estimate: 1d
- Depends on: B-004
- Deliverables:
  - stream assistant text to ElevenLabs
  - play audio chunks in browser
  - handle queue + end-of-utterance
- DoD: first audio starts <2.5s from end of user utterance.

## Milestone M2 — UX + Navigation Intelligence

### B-006 — Persona/SpeechInput UI Wiring (Vercel Elements)
- Type: checkpoint
- Priority: P1
- Estimate: 0.75d
- Depends on: B-005
- Deliverables:
  - Persona orb states wired to runtime state
  - SpeechInput state UI wired
- DoD: visual state always matches backend state.

### B-007 — Transcript Strip + Inline Action Chips
- Type: lesson
- Priority: P1
- Estimate: 0.75d
- Depends on: B-006
- Deliverables:
  - compact transcript strip (partial/final)
  - inline chips for `open_project`, `suggest_related`, `show_metrics`
- DoD: chips appear from structured events and are clickable.

### B-008 — Project Drawer + Link Routing
- Type: tool_call
- Priority: P1
- Estimate: 0.75d
- Depends on: B-007
- Deliverables:
  - project data JSON
  - drawer/modal for project details
  - chip click opens relevant project content
- DoD: “tell me about Line Lead” surfaces and opens Line Lead reliably.

## Milestone M3 — Policy, Gate, and Controls

### B-009 — Persona Prompt + Safety Policy Pack
- Type: decision
- Priority: P0
- Estimate: 0.75d
- Depends on: B-004
- Deliverables:
  - system prompt for first-person Johnny voice
  - blocked content policy (coworkers/private financials/secrets/PII)
  - fallback refusal templates
- DoD: red-team prompts do not leak blocked categories.

### B-010 — Email Gate + Session Caps
- Type: checkpoint
- Priority: P0
- Estimate: 0.75d
- Depends on: B-003
- Deliverables:
  - email gate modal after mic permission
  - max session duration (configurable, default 8 min)
  - graceful warning + auto-end
- DoD: no session starts without validated email.

### B-011 — Interrupt (Barge-in) Controls
- Type: tool_call
- Priority: P1
- Estimate: 0.5d
- Depends on: B-005
- Deliverables:
  - speaking interruption event
  - immediate TTS playback stop
  - return to listening state
- DoD: interruption response <300ms in local test.

## Milestone M4 — Observability + Launch

### B-012 — Telemetry + Cost Tracking
- Type: evidence
- Priority: P1
- Estimate: 0.75d
- Depends on: B-010, B-005
- Deliverables:
  - session count, duration, first-audio latency
  - action trigger counts
  - estimated cost/session
- DoD: daily summary view from logs.

### B-013 — Cross-browser QA + Polish
- Type: outcome
- Priority: P1
- Estimate: 1d
- Depends on: all above
- Deliverables:
  - Chrome/Safari/Edge smoke checks
  - error states and retries
  - UI polish pass
- DoD: launch checklist passes.

## Blockers / External Inputs
- ELEVENLABS_API_KEY + voice clone ID
- OPENAI_API_KEY or OAuth-backed realtime access in target runtime
- approved project content pack (Line Lead, Clawdio, Data Mentor, Aquaspec, PermitPro + existing)

## Execution Order
1) B-001 -> B-005 (backbone)
2) Parallel: B-009 + B-010
3) B-006 -> B-008 + B-011
4) B-012 -> B-013

## Target MVP Completion
- 7–9 dev days
