import { useEffect, useMemo, useRef, useState } from "react";
import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";
import { nextState, type VoiceEvent, type VoiceState } from "./lib/stateMachine";
import { VoiceWsClient } from "./lib/wsClient";

const DEFAULT_WS_URL = (import.meta.env.VITE_VOICE_SERVER_WS_URL as string) ?? "ws://localhost:8787";

export function App() {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [history, setHistory] = useState<string[]>(["idle"]);
  const [transcript, setTranscript] = useState<string[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<VoiceWsClient | null>(null);
  const audioQueueRef = useRef<string[]>([]);
  const isPlayingRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);

  const canToggle = state !== "requesting_mic" && state !== "connecting";
  const buttonLabel = state === "idle" || state === "error" ? "Start conversation" : "End conversation";

  function apply(event: VoiceEvent) {
    setState((prev) => {
      const next = nextState(prev, event);
      if (next !== prev) setHistory((h) => [...h, next]);
      return next;
    });
  }

  function stopPlayback() {
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.src = "";
      currentAudioRef.current = null;
    }
  }

  function playNextChunk() {
    if (isPlayingRef.current) return;
    const next = audioQueueRef.current.shift();
    if (!next) return;

    const audio = new Audio(`data:audio/mpeg;base64,${next}`);
    currentAudioRef.current = audio;
    isPlayingRef.current = true;

    const done = () => {
      isPlayingRef.current = false;
      currentAudioRef.current = null;
      playNextChunk();
    };

    audio.onended = done;
    audio.onerror = done;
    void audio.play().catch(() => done());
  }

  function onServerEvent(event: ServerEvent) {
    switch (event.type) {
      case "session.ready":
        setWsConnected(true);
        apply("CONNECTED");
        break;
      case "stt.partial":
        setTranscript((t) => [...t, `user(partial): ${event.text}`]);
        break;
      case "stt.final":
        setTranscript((t) => [...t, `user: ${event.text}`]);
        apply("ASSISTANT_THINKING");
        break;
      case "assistant.text.delta":
        setTranscript((t) => [...t, `assistant(delta): ${event.text}`]);
        apply("ASSISTANT_SPEAKING");
        break;
      case "assistant.text.final":
        setTranscript((t) => [...t, `assistant: ${event.text}`]);
        break;
      case "tts.audio.chunk":
        audioQueueRef.current.push(event.audioBase64);
        playNextChunk();
        break;
      case "tts.done":
        apply("END_SPEAKING");
        break;
      case "error":
        setError(`${event.code}: ${event.message}`);
        apply("FAIL");
        break;
      case "session.ended":
        apply("END");
        setWsConnected(false);
        break;
      default:
        break;
    }
  }

  function send(event: ClientEvent) {
    wsRef.current?.send(event);
  }

  async function requestMic() {
    setError(null);
    apply("START");
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      apply("MIC_GRANTED");
    } catch {
      setError("Microphone permission denied");
      apply("MIC_DENIED");
    }
  }

  async function handleToggle() {
    if (!canToggle) return;

    if (state === "idle" || state === "error") {
      await requestMic();
      return;
    }

    stopPlayback();
    send({ type: "session.stop", reason: "user" });
    wsRef.current?.close();
    wsRef.current = null;
    setWsConnected(false);
    apply("END");
  }

  function submitEmail() {
    if (!email.includes("@")) {
      setError("Enter a valid email to continue");
      return;
    }
    setError(null);
    apply("EMAIL_VERIFIED");

    const client = new VoiceWsClient(DEFAULT_WS_URL, onServerEvent);
    wsRef.current = client;
    client.onOpen(() => {
      wsRef.current?.send({ type: "session.start", email });
    });
    client.connect();
  }

  useEffect(() => {
    return () => {
      stopPlayback();
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  const debug = useMemo(
    () => ({
      state,
      canToggle,
      hasMic: state !== "idle",
      isGated: state === "gated",
      email,
      wsUrl: DEFAULT_WS_URL,
      wsConnected,
      transitions: history,
      transcriptTail: transcript.slice(-6)
    }),
    [state, canToggle, email, history, transcript, wsConnected]
  );

  return (
    <main style={{ fontFamily: "Inter, system-ui, sans-serif", maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <h1>Portfolio Voice Agent — Scaffold</h1>
      <p>Beads B-001..B-004: state machine + websocket + realtime adapter wiring.</p>

      <section style={{ marginTop: 24, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={handleToggle} disabled={!canToggle} style={{ padding: "10px 16px", borderRadius: 8 }}>
          {buttonLabel}
        </button>
        <span><strong>State:</strong> {state}</span>
        <span><strong>WS:</strong> {wsConnected ? "connected" : "disconnected"}</span>
      </section>

      {state === "gated" && (
        <section style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="email"
            placeholder="recruiter@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid #ccc", minWidth: 260 }}
          />
          <button onClick={submitEmail} style={{ padding: "8px 12px" }}>Verify Email + Connect</button>
        </section>
      )}

      {state === "listening" && (
        <section style={{ marginTop: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button onClick={() => send({ type: "voice.input.end_turn" })} style={{ padding: "8px 10px" }}>
            Send end_turn (test)
          </button>
          <button onClick={() => { stopPlayback(); send({ type: "voice.interrupt" }); }} style={{ padding: "8px 10px" }}>
            Interrupt
          </button>
        </section>
      )}

      {error && <p style={{ color: "#b91c1c", marginTop: 12 }}>{error}</p>}

      <section style={{ marginTop: 28 }}>
        <h2>Debug Panel</h2>
        <pre style={{ background: "#111", color: "#0f0", padding: 12, borderRadius: 8, overflowX: "auto" }}>
          {JSON.stringify(debug, null, 2)}
        </pre>
      </section>
    </main>
  );
}
