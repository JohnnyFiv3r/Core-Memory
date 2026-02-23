import { useEffect, useMemo, useRef, useState } from "react";
import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";
import { nextState, type VoiceEvent, type VoiceState } from "./lib/stateMachine";
import { VoiceWsClient } from "./lib/wsClient";
import { PersonaOrb } from "./components/PersonaOrb";
import { TranscriptStrip } from "./components/TranscriptStrip";

const DEFAULT_WS_URL = (import.meta.env.VITE_VOICE_SERVER_WS_URL as string) ?? "ws://localhost:8787";

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function App() {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [history, setHistory] = useState<string[]>(["idle"]);
  const [transcript, setTranscript] = useState<string[]>([]);
  const [actionChips, setActionChips] = useState<Array<{ label: string; slug?: string }>>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [ttsDebug, setTtsDebug] = useState<string[]>([]);
  const [audioUnlocked, setAudioUnlocked] = useState(false);

  const wsRef = useRef<VoiceWsClient | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const mediaSourceRef = useRef<MediaSource | null>(null);
  const sourceBufferRef = useRef<SourceBuffer | null>(null);
  const appendQueueRef = useRef<Uint8Array[]>([]);
  const ttsDoneRef = useRef(false);
  const mediaUrlRef = useRef<string | null>(null);
  const fallbackChunksRef = useRef<Uint8Array[]>([]);
  const playbackStartedRef = useRef(false);

  const canToggle = state !== "requesting_mic" && state !== "connecting";
  const buttonLabel = state === "idle" || state === "error" ? "Start conversation" : "End conversation";

  function apply(event: VoiceEvent) {
    setState((prev) => {
      const next = nextState(prev, event);
      if (next !== prev) setHistory((h) => [...h, next]);
      return next;
    });
  }

  function pushTtsDebug(line: string) {
    setTtsDebug((prev) => [...prev.slice(-24), line]);
  }

  function flushAppendQueue() {
    const sourceBuffer = sourceBufferRef.current;
    const mediaSource = mediaSourceRef.current;
    if (!sourceBuffer || !mediaSource) return;
    if (sourceBuffer.updating) return;
    if (mediaSource.readyState !== "open") return;

    const next = appendQueueRef.current.shift();
    if (next) {
      try {
        const chunk = next.buffer.slice(next.byteOffset, next.byteOffset + next.byteLength) as ArrayBuffer;
        sourceBuffer.appendBuffer(chunk);
      } catch {
        // stale/removed source buffer race; drop chunk and keep stream alive
      }
      return;
    }

    if (ttsDoneRef.current && mediaSource.readyState === "open") {
      try {
        mediaSource.endOfStream();
      } catch {
        // noop
      }
    }
  }

  async function ensureStreamingAudioStarted() {
    // Streaming path disabled for now due to browser-specific MSE/MP3 quirks.
    // We keep collecting chunks and play once at tts.done via fallback blob.
    return;
  }

  function startNewTtsStream() {
    stopPlayback();
    ttsDoneRef.current = false;
    appendQueueRef.current = [];
    fallbackChunksRef.current = [];
    playbackStartedRef.current = false;
  }

  function appendTtsChunk(base64: string) {
    if (ttsDoneRef.current) return;
    const bytes = base64ToBytes(base64);
    fallbackChunksRef.current.push(bytes);
  }

  async function markTtsDone() {
    ttsDoneRef.current = true;

    // Primary playback path: play full utterance blob.
    if (fallbackChunksRef.current.length > 0) {
      const size = fallbackChunksRef.current.reduce((acc, c) => acc + c.byteLength, 0);
      const merged = new Uint8Array(size);
      let offset = 0;
      for (const c of fallbackChunksRef.current) {
        merged.set(c, offset);
        offset += c.byteLength;
      }
      const blob = new Blob([merged.buffer], { type: "audio/mpeg" });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      currentAudioRef.current = audio;
      try {
        await audio.play();
        pushTtsDebug("[audio.fallback.play] fallback blob playback started");
      } catch (e) {
        pushTtsDebug(`[audio.fallback.error] ${String((e as Error)?.message ?? e)}`);
      }
      audio.onended = () => URL.revokeObjectURL(url);
    }
  }

  function stopPlayback() {
    appendQueueRef.current = [];
    fallbackChunksRef.current = [];
    ttsDoneRef.current = false;
    playbackStartedRef.current = false;

    if (sourceBufferRef.current) {
      try {
        sourceBufferRef.current.abort();
      } catch {
        // noop
      }
    }

    if (mediaSourceRef.current?.readyState === "open") {
      try {
        mediaSourceRef.current.endOfStream();
      } catch {
        // noop
      }
    }

    sourceBufferRef.current = null;
    mediaSourceRef.current = null;

    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.src = "";
      currentAudioRef.current = null;
    }

    if (mediaUrlRef.current) {
      URL.revokeObjectURL(mediaUrlRef.current);
      mediaUrlRef.current = null;
    }
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
        startNewTtsStream();
        break;
      case "assistant.action": {
        if (event.action === "open_project") {
          const slug = String((event.payload as any).slug ?? "project");
          const label = String((event.payload as any).label ?? `Open ${slug}`);
          setActionChips((prev) => [...prev.slice(-4), { label, slug }]);
        }
        if (event.action === "suggest_related") {
          const slugs = Array.isArray((event.payload as any).slugs) ? (event.payload as any).slugs : [];
          const chips = slugs.slice(0, 3).map((slug: string) => ({ label: `Explore ${slug}`, slug }));
          if (chips.length) setActionChips((prev) => [...prev.slice(-2), ...chips]);
        }
        break;
      }
      case "tts.audio.chunk":
        appendTtsChunk(event.audioBase64);
        break;
      case "tts.done":
        markTtsDone();
        apply("END_SPEAKING");
        break;
      case "debug.tts":
        pushTtsDebug(`[${event.stage}] ${event.detail ?? ""}`.trim());
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

  async function unlockAudioOutput() {
    try {
      const Ctx = (window.AudioContext || (window as any).webkitAudioContext);
      if (!Ctx) {
        pushTtsDebug("[audio.unlock] AudioContext not supported");
        return;
      }
      const ctx = new Ctx();
      await ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 440;
      gain.gain.value = 0.0001;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.03);
      setAudioUnlocked(true);
      pushTtsDebug("[audio.unlock] Audio output unlocked by user gesture");
    } catch (e) {
      pushTtsDebug(`[audio.unlock.error] ${String((e as Error)?.message ?? e)}`);
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
      transcriptTail: transcript.slice(-6),
      ttsDebugTail: ttsDebug.slice(-8)
    }),
    [state, canToggle, email, history, transcript, wsConnected, ttsDebug]
  );

  return (
    <main style={{ fontFamily: "Inter, system-ui, sans-serif", maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <h1>Portfolio Voice Agent</h1>
      <p>Beads B-001..B-007: realtime backbone + persona states + transcript + action chips.</p>
      <div style={{ marginTop: 12 }}>
        <PersonaOrb state={state} />
      </div>

      <section style={{ marginTop: 24, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={handleToggle} disabled={!canToggle} style={{ padding: "10px 16px", borderRadius: 8 }}>
          {buttonLabel}
        </button>
        <button onClick={unlockAudioOutput} style={{ padding: "10px 16px", borderRadius: 8 }}>
          {audioUnlocked ? "Audio unlocked ✅" : "Unlock audio"}
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

      <TranscriptStrip lines={transcript} />

      <section style={{ marginTop: 14 }}>
        <h3 style={{ margin: "0 0 8px 0" }}>ElevenLabs Debug</h3>
        <pre style={{ background: "#0b1020", color: "#c7d2fe", padding: 10, borderRadius: 8, minHeight: 64 }}>
{ttsDebug.length ? ttsDebug.join("\n") : "No TTS debug events yet."}
        </pre>
      </section>

      <section style={{ marginTop: 14 }}>
        <h3 style={{ margin: "0 0 8px 0" }}>Action Chips</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {actionChips.length === 0 ? (
            <small style={{ color: "#71717a" }}>No project suggestions yet.</small>
          ) : (
            actionChips.map((chip, idx) => (
              <button
                key={`${chip.label}-${idx}`}
                onClick={() => setTranscript((t) => [...t, `ui-action: ${chip.slug ?? chip.label}`])}
                style={{
                  padding: "6px 10px",
                  borderRadius: 999,
                  border: "1px solid #d4d4d8",
                  background: "white",
                  fontSize: 12
                }}
              >
                {chip.label}
              </button>
            ))
          )}
        </div>
      </section>

      <section style={{ marginTop: 28 }}>
        <h2>Debug Panel</h2>
        <pre style={{ background: "#111", color: "#0f0", padding: 12, borderRadius: 8, overflowX: "auto" }}>
          {JSON.stringify(debug, null, 2)}
        </pre>
      </section>
    </main>
  );
}
