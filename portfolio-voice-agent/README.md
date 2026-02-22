# Portfolio Voice Agent (v1 Scaffold)

Implements beads B-001..B-007:
- B-001: monorepo scaffold (`apps/web`, `apps/voice-server`, `packages/shared-types`)
- B-002: shared websocket event schemas (Zod)
- B-003: web mic permission + session state machine with visible debug panel
- B-004: OpenAI Realtime server adapter (audio in, text deltas/finals out)
- B-005: ElevenLabs streaming TTS chunks + browser playback queue + interrupt stop
- B-006: Persona orb state wiring for UX feedback
- B-007: Transcript strip + inline action chips (project suggestions)

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
- OpenAI Realtime is wired on the voice-server side.
- ElevenLabs streaming playback is wired (server emits `tts.audio.chunk`, web queues/plays chunks).
- Event payloads are validated against shared zod schemas on the voice server.
- For full voice output, set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `apps/voice-server/.env`.
