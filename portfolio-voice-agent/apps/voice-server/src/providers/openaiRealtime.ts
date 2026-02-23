import WebSocket from "ws";
import type { ServerEvent } from "@portfolio/shared-types";

type Emit = (event: ServerEvent) => void;

/**
 * Thin OpenAI Realtime adapter for B-004:
 * - audio input buffer append/commit
 * - response.create (text output)
 * - forwards transcript + assistant text deltas
 */
export class OpenAIRealtimeSession {
  private upstream: WebSocket;
  private closed = false;

  constructor(private emit: Emit) {
    const apiKey = process.env.OPENAI_API_KEY;
    if (!apiKey) throw new Error("OPENAI_API_KEY is required for realtime");

    const model = process.env.OPENAI_REALTIME_MODEL ?? "gpt-4o-realtime-preview";
    const url = `wss://api.openai.com/v1/realtime?model=${encodeURIComponent(model)}`;

    this.upstream = new WebSocket(url, {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "OpenAI-Beta": "realtime=v1"
      }
    });

    this.upstream.on("open", () => {
      // Configure session for text responses; UI TTS is handled separately (B-005+).
      this.send({
        type: "session.update",
        session: {
          modalities: ["text"],
          instructions:
            "You are Johnny's portfolio voice assistant. Always respond in English unless the user explicitly asks for another language. Be concise, first-person, and helpful.",
          input_audio_format: "pcm16",
          output_audio_format: "pcm16",
          input_audio_transcription: {
            model: "gpt-4o-mini-transcribe",
            language: "en"
          },
          turn_detection: {
            type: "server_vad",
            threshold: 0.5,
            prefix_padding_ms: 300,
            silence_duration_ms: 500
          }
        }
      });
    });

    this.upstream.on("message", (raw) => {
      this.handleUpstreamMessage(raw.toString());
    });

    this.upstream.on("error", (err) => {
      this.emit({ type: "error", code: "realtime_upstream_error", message: String(err.message ?? err) });
    });

    this.upstream.on("close", () => {
      if (!this.closed) {
        this.emit({ type: "error", code: "realtime_closed", message: "OpenAI realtime session closed" });
      }
    });
  }

  appendAudioChunk(base64Pcm16: string) {
    this.send({ type: "input_audio_buffer.append", audio: base64Pcm16 });
  }

  endTurn() {
    this.send({ type: "input_audio_buffer.commit" });
    this.send({ type: "response.create", response: { modalities: ["text"] } });
  }

  interrupt() {
    this.send({ type: "response.cancel" });
  }

  close() {
    this.closed = true;
    try {
      this.upstream.close();
    } catch {
      // noop
    }
  }

  private send(payload: unknown) {
    if (this.upstream.readyState === WebSocket.OPEN) {
      this.upstream.send(JSON.stringify(payload));
    }
  }

  private handleUpstreamMessage(json: string) {
    let event: any;
    try {
      event = JSON.parse(json);
    } catch {
      return;
    }

    // Known/likely realtime event mappings (defensive):
    if (event.type === "conversation.item.input_audio_transcription.completed") {
      const text = String(event.transcript ?? "").trim();
      if (text) this.emit({ type: "stt.final", text });
      return;
    }

    if (event.type === "response.text.delta") {
      const text = String(event.delta ?? "");
      if (text) this.emit({ type: "assistant.text.delta", text });
      return;
    }

    if (event.type === "response.text.done") {
      const text = String(event.text ?? "").trim();
      if (text) this.emit({ type: "assistant.text.final", text });
      return;
    }

    if (event.type === "response.done") {
      return;
    }
  }
}
