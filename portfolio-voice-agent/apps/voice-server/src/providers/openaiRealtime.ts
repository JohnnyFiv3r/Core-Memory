import WebSocket from "ws";
import type { ServerEvent } from "@portfolio/shared-types";
import { selectStory } from "./storySelector";
import { buildRealtimeInstructions } from "./promptLoader";

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
      const instructions = buildRealtimeInstructions();

      // Configure session for text responses; UI TTS is handled separately (B-005+).
      this.send({
        type: "session.update",
        session: {
          modalities: ["text"],
          instructions,
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
            silence_duration_ms: 500,
            create_response: true,
            interrupt_response: true
          },
          tool_choice: "auto",
          tools: [
            {
              type: "function",
              name: "select_story",
              description:
                "Select the most relevant portfolio story only when user explicitly asks for examples/background/project context. Return none when not needed.",
              parameters: {
                type: "object",
                properties: {
                  user_query: { type: "string", description: "User question or utterance" },
                  mode: {
                    type: "string",
                    enum: ["recruiter", "technical", "founder", "investor", "general"],
                    description: "Optional conversational mode"
                  }
                },
                required: ["user_query"],
                additionalProperties: false
              }
            }
          ]
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

  private handleSelectStoryToolCall(callId: string | undefined, argsRaw: string | undefined) {
    if (!callId) return;

    let args: any = {};
    try {
      args = argsRaw ? JSON.parse(argsRaw) : {};
    } catch {
      args = {};
    }

    const userQuery = String(args?.user_query ?? "");
    const result = selectStory(userQuery);

    this.emit({
      type: "debug.tts",
      stage: "story.selected",
      detail: `id=${result.story_id} confidence=${result.confidence.toFixed(2)} reason=${result.reason}`
    });

    this.send({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(result)
      }
    });

    this.send({
      type: "response.create",
      response: { modalities: ["text"] }
    });
  }

  private handleUpstreamMessage(json: string) {
    let event: any;
    try {
      event = JSON.parse(json);
    } catch {
      return;
    }

    // Realtime function calling variants
    if (event.type === "response.function_call_arguments.done" && event.name === "select_story") {
      this.handleSelectStoryToolCall(event.call_id, event.arguments);
      return;
    }

    if (event.type === "response.output_item.done" && event.item?.type === "function_call" && event.item?.name === "select_story") {
      this.handleSelectStoryToolCall(event.item.call_id, event.item.arguments);
      return;
    }

    // Known/likely realtime event mappings (defensive):
    if (event.type === "conversation.item.input_audio_transcription.delta") {
      const text = String(event.delta ?? "").trim();
      if (text) this.emit({ type: "stt.partial", text });
      return;
    }

    if (event.type === "conversation.item.input_audio_transcription.completed") {
      const text = String(event.transcript ?? "").trim();
      if (text) this.emit({ type: "stt.final", text });
      return;
    }

    if (event.type === "input_audio_buffer.speech_started") {
      this.emit({ type: "stt.partial", text: "Listening…" });
      return;
    }

    if (event.type === "input_audio_buffer.speech_stopped") {
      this.emit({ type: "stt.partial", text: "Processing…" });
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
