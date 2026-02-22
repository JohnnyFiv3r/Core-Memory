import "dotenv/config";
import { randomUUID } from "node:crypto";
import { WebSocketServer } from "ws";
import {
  ClientEventSchema,
  ServerEventSchema,
  type ClientEvent,
  type ServerEvent
} from "@portfolio/shared-types";

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
  send(ws, { type: "session.ready", sessionId: randomUUID(), maxMinutes: Number(process.env.MAX_SESSION_MINUTES ?? 8) });

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

    handleClientEvent(ws, parsed.data);
  });
});

function handleClientEvent(ws: import("ws").WebSocket, event: ClientEvent) {
  switch (event.type) {
    case "session.start":
      send(ws, { type: "stt.partial", text: "(placeholder) listening…" });
      break;
    case "voice.input.end_turn":
      send(ws, { type: "assistant.text.delta", text: "(placeholder) Provider integrations land in B-004." });
      send(ws, { type: "assistant.text.final", text: "(placeholder) Provider integrations land in B-004." });
      send(ws, { type: "tts.done" });
      break;
    case "session.stop":
      send(ws, { type: "session.ended", reason: "user" });
      break;
    default:
      // noop placeholders for this phase
      break;
  }
}

console.log(`[voice-server] listening on ws://localhost:${port}`);
