# Portfolio Voice Agent (v1 Scaffold)

Implements beads B-001..B-003:
- B-001: monorepo scaffold (`apps/web`, `apps/voice-server`, `packages/shared-types`)
- B-002: shared websocket event schemas (Zod)
- B-003: web mic permission + session state machine with visible debug panel

## Prerequisites
- Node 20+
- pnpm 10+

## Setup
```bash
cd portfolio-voice-agent
pnpm install
```

## Run (web + voice server)
```bash
pnpm dev
```
- Web: http://localhost:5173
- Voice server WS: ws://localhost:8787

## Build
```bash
pnpm build
```

## Environment
- `apps/web/.env.example`
- `apps/voice-server/.env.example`

## Notes
- Provider integrations (OpenAI Realtime, ElevenLabs) are placeholders only in this phase.
- Event payloads are validated against shared zod schemas on the voice server.
