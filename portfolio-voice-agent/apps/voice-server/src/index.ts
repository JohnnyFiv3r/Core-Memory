import "dotenv/config";
import { randomUUID } from "node:crypto";
import { WebSocketServer } from "ws";
import {
  ClientEventSchema,
  ServerEventSchema,
  type ClientEvent,
  type ServerEvent
} from "@portfolio/shared-types";
import { OpenAIRealtimeSession } from "./providers/openaiRealtime";
import { ElevenLabsTts } from "./providers/elevenlabs";

const port = Number(process.env.PORT ?? 8787);
const wss = new WebSocketServer({ port });

function send(ws: import("ws").WebSocket, event: ServerEvent) {
  const parsed = ServerEventSchema.safeParse(event);
  if (!parsed.success) {
    console.error("Invalid server event", parsed.error.flatten());
    return;
  }
  ws.send(JSON.stringify(parsed.data));
}

wss.on("connection", (ws) => {
  const sessionId = randomUUID();
  let realtime: OpenAIRealtimeSession | null = null;
  let ttsAbort: AbortController | null = null;
  let ttsCounter = 0;
  const tts = buildTts();

  const emit = (event: ServerEvent) => {
    send(ws, event);

    // B-005: stream ElevenLabs audio from assistant final text
    if (event.type === "assistant.text.final") {
      send(ws, { type: "debug.tts", stage: "assistant.text.final", detail: `chars=${event.text.length}` });
      if (tts) {
        const ttsId = ++ttsCounter;
        void streamAssistantTts(event.text, ttsId);
      } else {
        send(ws, { type: "debug.tts", stage: "tts.skipped", detail: "ElevenLabs not configured (missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID)" });
      }
      // B-007: lightweight action chip suggestions from project mentions
      const projectSlugs = ["line-lead", "clawdio", "data-mentor", "aquaspec", "permitpro", "storyboard", "midwest-muscle", "contour"]; 
      const lowered = event.text.toLowerCase();
      const matched = projectSlugs.find((slug) => lowered.includes(slug.replace("-", " ")) || lowered.includes(slug));
      if (matched) {
        emit({
          type: "assistant.action",
          action: "open_project",
          payload: { slug: matched, label: `Open ${matched}` }
        });
      }
    }
  };

  async function streamAssistantTts(text: string, ttsId: number) {
    try {
      ttsAbort?.abort();
      ttsAbort = new AbortController();
      let chunkCount = 0;
      let totalBytes = 0;
      send(ws, { type: "debug.tts", stage: "tts.start", detail: `ttsId=${ttsId} chars=${text.length}` });
      await tts!.streamSpeak(
        text,
        (audioBase64) => {
          chunkCount += 1;
          totalBytes += Buffer.from(audioBase64, "base64").byteLength;
          emit({ type: "tts.audio.chunk", ttsId, audioBase64, mime: "audio/mpeg" });
        },
        ttsAbort.signal
      );
      send(ws, { type: "debug.tts", stage: "tts.done", detail: `ttsId=${ttsId} chunks=${chunkCount} bytes=${totalBytes}` });
      emit({ type: "tts.done", ttsId });
    } catch (error: any) {
      if (ttsAbort?.signal.aborted) {
        emit({ type: "tts.done", ttsId });
        return;
      }
      const message = String(error?.message ?? error);
      send(ws, { type: "debug.tts", stage: "tts.error", detail: message });
      emit({
        type: "error",
        code: "tts_stream_failed",
        message
      });
    }
  }

  send(ws, { type: "session.ready", sessionId, maxMinutes: Number(process.env.MAX_SESSION_MINUTES ?? 8) });

  ws.on("message", (raw) => {
    let payload: unknown;
    try {
      payload = JSON.parse(raw.toString());
    } catch {
      send(ws, { type: "error", code: "bad_json", message: "Message must be valid JSON" });
      return;
    }

    const parsed = ClientEventSchema.safeParse(payload);
    if (!parsed.success) {
      send(ws, { type: "error", code: "invalid_event", message: "Event payload failed schema validation" });
      return;
    }

    const event = parsed.data;
    realtime = handleClientEvent(ws, event, realtime, emit, () => {
      ttsAbort?.abort();
      ttsAbort = null;
    });
  });

  ws.on("close", () => {
    ttsAbort?.abort();
    realtime?.close();
  });
});

function handleClientEvent(
  ws: import("ws").WebSocket,
  event: ClientEvent,
  realtime: OpenAIRealtimeSession | null,
  emit: (event: ServerEvent) => void,
  stopTts: () => void
): OpenAIRealtimeSession | null {
  switch (event.type) {
    case "session.start": {
      if (!realtime) {
        try {
          realtime = new OpenAIRealtimeSession((serverEvent) => emit(serverEvent));
          emit({ type: "stt.partial", text: "Realtime connected. Listening…" });
        } catch (error: any) {
          emit({
            type: "error",
            code: "realtime_init_failed",
            message: String(error?.message ?? error)
          });
        }
      }
      return realtime;
    }
    case "voice.input.chunk": {
      if (!realtime) {
        emit({ type: "error", code: "session_not_started", message: "Send session.start before audio chunks" });
        return realtime;
      }
      realtime.appendAudioChunk(event.pcm16Base64);
      return realtime;
    }
    case "voice.input.end_turn": {
      if (!realtime) {
        emit({ type: "error", code: "session_not_started", message: "Send session.start before end_turn" });
        return realtime;
      }
      realtime.endTurn();
      return realtime;
    }
    case "voice.interrupt": {
      stopTts();
      realtime?.interrupt();
      return realtime;
    }
    case "session.stop": {
      stopTts();
      realtime?.close();
      emit({ type: "session.ended", reason: "user" });
      return null;
    }
    default:
      return realtime;
  }
}

function buildTts(): ElevenLabsTts | null {
  try {
    return new ElevenLabsTts();
  } catch {
    return null;
  }
}

console.log(`[voice-server] listening on ws://localhost:${port}`);
