import "dotenv/config";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
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

const httpServer = createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? "/", `http://localhost:${port}`);

    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
      });
      res.end();
      return;
    }

    if (req.method === "GET" && url.pathname === "/oauth/webflow/exchange") {
      const code = url.searchParams.get("code");
      if (!code) {
        res.writeHead(400, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
        res.end(JSON.stringify({ ok: false, error: "Missing code" }));
        return;
      }

      const tokenData = await exchangeWebflowCode(code);
      await persistWebflowToken(tokenData);

      res.writeHead(200, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
      res.end(JSON.stringify({
        ok: true,
        tokenType: tokenData.token_type,
        scope: tokenData.scope ?? null,
        workspaceId: tokenData.workspace_id ?? null
      }));
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
    res.end(JSON.stringify({ ok: false, error: "Not found" }));
  } catch (error: any) {
    res.writeHead(500, { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" });
    res.end(JSON.stringify({ ok: false, error: String(error?.message ?? error) }));
  }
});

const wss = new WebSocketServer({ server: httpServer });
const currentDir = dirname(fileURLToPath(import.meta.url));

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
  if (tts) {
    const cfg = tts.getDebugConfig();
    send(ws, {
      type: "debug.tts",
      stage: "tts.config",
      detail: `voiceId=${cfg.voiceId} model=${cfg.modelId} output=${cfg.outputFormat} stability=${cfg.stability} similarity=${cfg.similarityBoost} style=${cfg.style} speakerBoost=${cfg.useSpeakerBoost}`
    });
  }

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

  const maxMinutes = Number(process.env.MAX_SESSION_MINUTES ?? 5);
  const maxTurns = Number(process.env.MAX_SESSION_TURNS ?? 20);
  let turnCount = 0;
  let sessionEnded = false;

  function enforceSessionEnd() {
    if (sessionEnded) return;
    sessionEnded = true;
    ttsAbort?.abort();
    realtime?.close();
    send(ws, { type: "session.ended", reason: "limit" });
    try { ws.close(); } catch {}
  }

  // Time limit
  const sessionTimer = setTimeout(() => enforceSessionEnd(), maxMinutes * 60 * 1000);

  send(ws, { type: "session.ready", sessionId, maxMinutes });

  ws.on("message", (raw) => {
    if (sessionEnded) return;

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

    // Count turns on end_turn (user finished speaking)
    if (event.type === "voice.input.end_turn") {
      turnCount++;
      if (turnCount >= maxTurns) {
        enforceSessionEnd();
        return;
      }
    }

    realtime = handleClientEvent(ws, event, realtime, emit, () => {
      ttsAbort?.abort();
      ttsAbort = null;
    });
  });

  ws.on("close", () => {
    clearTimeout(sessionTimer);
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

async function exchangeWebflowCode(code: string) {
  const clientId = process.env.WEBFLOW_CLIENT_ID;
  const clientSecret = process.env.WEBFLOW_CLIENT_SECRET;
  const redirectUri = process.env.WEBFLOW_REDIRECT_URI;

  if (!clientId || !clientSecret || !redirectUri) {
    throw new Error("Missing WEBFLOW_CLIENT_ID / WEBFLOW_CLIENT_SECRET / WEBFLOW_REDIRECT_URI");
  }

  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    code,
    grant_type: "authorization_code",
    redirect_uri: redirectUri
  });

  const response = await fetch("https://api.webflow.com/oauth/access_token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body
  });

  const raw = await response.text();
  let json: any;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new Error(`Webflow token exchange returned non-JSON (${response.status})`);
  }

  if (!response.ok) {
    throw new Error(`Webflow token exchange failed (${response.status}): ${json?.message ?? raw}`);
  }

  if (!json?.access_token) {
    throw new Error("Webflow token exchange response missing access_token");
  }

  return json as {
    access_token: string;
    token_type?: string;
    scope?: string;
    workspace_id?: string;
    [k: string]: unknown;
  };
}

async function persistWebflowToken(tokenData: { access_token: string; [k: string]: unknown }) {
  const out = resolve(currentDir, "../../../../.openclaw/secrets/webflow-token.json");
  await mkdir(dirname(out), { recursive: true });
  const payload = {
    savedAt: new Date().toISOString(),
    ...tokenData
  };
  await writeFile(out, JSON.stringify(payload, null, 2), { mode: 0o600 });
}

function buildTts(): ElevenLabsTts | null {
  try {
    return new ElevenLabsTts();
  } catch {
    return null;
  }
}

httpServer.listen(port, () => {
  console.log(`[voice-server] listening on ws://localhost:${port}`);
  console.log(`[voice-server] oauth exchange endpoint: http://localhost:${port}/oauth/webflow/exchange?code=...`);
});
