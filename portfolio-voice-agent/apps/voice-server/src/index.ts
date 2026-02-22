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
    realtime = handleClientEvent(ws, event, realtime);
  });

  ws.on("close", () => {
    realtime?.close();
  });
});

function handleClientEvent(
  ws: import("ws").WebSocket,
  event: ClientEvent,
  realtime: OpenAIRealtimeSession | null
): OpenAIRealtimeSession | null {
  switch (event.type) {
    case "session.start": {
      // Lazy init per user session
      if (!realtime) {
        try {
          realtime = new OpenAIRealtimeSession((serverEvent) => send(ws, serverEvent));
          send(ws, { type: "stt.partial", text: "Realtime connected. Listening…" });
        } catch (error: any) {
          send(ws, {
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
        send(ws, { type: "error", code: "session_not_started", message: "Send session.start before audio chunks" });
        return realtime;
      }
      realtime.appendAudioChunk(event.pcm16Base64);
      return realtime;
    }
    case "voice.input.end_turn": {
      if (!realtime) {
        send(ws, { type: "error", code: "session_not_started", message: "Send session.start before end_turn" });
        return realtime;
      }
      realtime.endTurn();
      return realtime;
    }
    case "voice.interrupt": {
      realtime?.interrupt();
      return realtime;
    }
    case "session.stop": {
      realtime?.close();
      send(ws, { type: "session.ended", reason: "user" });
      return null;
    }
    default:
      return realtime;
  }
}

console.log(`[voice-server] listening on ws://localhost:${port}`);
