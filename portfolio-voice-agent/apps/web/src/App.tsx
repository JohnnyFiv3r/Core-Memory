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

  const stateRef = useRef<VoiceState>("idle");

  const wsRef = useRef<VoiceWsClient | null>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const mediaSourceRef = useRef<MediaSource | null>(null);
  const sourceBufferRef = useRef<SourceBuffer | null>(null);
  const appendQueueRef = useRef<Uint8Array[]>([]);
  const ttsDoneRef = useRef(false);
  const mediaUrlRef = useRef<string | null>(null);
  const fallbackChunksRef = useRef<Uint8Array[]>([]);
  const playbackStartedRef = useRef(false);
  const doneRetryTimerRef = useRef<number | null>(null);
  const ttsPlaybackLaunchedRef = useRef(false);
  const ttsPlaybackBytesRef = useRef(0);
  const ttsDoneRetryCountRef = useRef(0);
  const activeTtsIdRef = useRef<number | null>(null);

  const micStreamRef = useRef<MediaStream | null>(null);
  const micContextRef = useRef<AudioContext | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const micSeqRef = useRef(0);

  const canToggle = state !== "requesting_mic" && state !== "connecting";
  const buttonLabel = state === "idle" || state === "error" ? "Start conversation" : "End conversation";

  function apply(event: VoiceEvent) {
    setState((prev) => {
      const next = nextState(prev, event);
      stateRef.current = next;
      if (next !== prev) setHistory((h) => [...h, next]);
      return next;
    });
  }

  function pushTtsDebug(line: string) {
    setTtsDebug((prev) => [...prev.slice(-24), line]);
  }

  function floatToPcm16Base64(input: Float32Array, inputSampleRate: number, outputSampleRate = 24000): string {
    const ratio = inputSampleRate / outputSampleRate;
    const newLength = Math.max(1, Math.floor(input.length / ratio));
    const pcm = new Int16Array(newLength);

    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < newLength) {
      const nextOffsetBuffer = Math.floor((offsetResult + 1) * ratio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < input.length; i++) {
        accum += input[i];
        count++;
      }
      const sample = count > 0 ? accum / count : 0;
      const clamped = Math.max(-1, Math.min(1, sample));
      pcm[offsetResult] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }

    const bytes = new Uint8Array(pcm.buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function stopMicStreaming() {
    try { micProcessorRef.current?.disconnect(); } catch {}
    try { micSourceRef.current?.disconnect(); } catch {}
    try { micContextRef.current?.close(); } catch {}
    try { micStreamRef.current?.getTracks().forEach((t) => t.stop()); } catch {}
    micProcessorRef.current = null;
    micSourceRef.current = null;
    micContextRef.current = null;
    micStreamRef.current = null;
  }

  async function startMicStreaming() {
    stopMicStreaming();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    micSeqRef.current = 0;
    processor.onaudioprocess = (event) => {
      const ws = wsRef.current;
      if (!ws) return;
      const s = stateRef.current;
      if (!(s === "listening" || s === "thinking" || s === "speaking")) return;
      const channelData = event.inputBuffer.getChannelData(0);
      const base64 = floatToPcm16Base64(channelData, audioContext.sampleRate, 24000);
      ws.send({ type: "voice.input.chunk", pcm16Base64: base64, seq: micSeqRef.current++ });
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    micStreamRef.current = stream;
    micContextRef.current = audioContext;
    micSourceRef.current = source;
    micProcessorRef.current = processor;
    pushTtsDebug(`[mic.started] sampleRate=${audioContext.sampleRate}`);
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
    ttsPlaybackLaunchedRef.current = false;
    ttsPlaybackBytesRef.current = 0;
    ttsDoneRetryCountRef.current = 0;
    activeTtsIdRef.current = null;
    if (doneRetryTimerRef.current) {
      window.clearTimeout(doneRetryTimerRef.current);
      doneRetryTimerRef.current = null;
    }
  }

  function appendTtsChunk(base64: string, ttsId: number) {
    if (activeTtsIdRef.current === null) {
      activeTtsIdRef.current = ttsId;
      pushTtsDebug(`[tts.bind] active ttsId=${ttsId}`);
    }
    if (ttsId !== activeTtsIdRef.current) {
      pushTtsDebug(`[tts.ignore] stale chunk ttsId=${ttsId}, active=${activeTtsIdRef.current}`);
      return;
    }

    const bytes = base64ToBytes(base64);
    fallbackChunksRef.current.push(bytes);
    if (ttsDoneRef.current) {
      pushTtsDebug(`[audio.late_chunk] accepted chunk after tts.done (bytes=${bytes.byteLength})`);
      if (!ttsPlaybackLaunchedRef.current) {
        if (doneRetryTimerRef.current) {
          window.clearTimeout(doneRetryTimerRef.current);
          doneRetryTimerRef.current = null;
        }
        void markTtsDone();
      }
    }
  }

  async function markTtsDone() {
    ttsDoneRef.current = true;

    if (fallbackChunksRef.current.length === 0) {
      ttsDoneRetryCountRef.current += 1;
      if (ttsDoneRetryCountRef.current > 20) {
        pushTtsDebug("[audio.fallback.timeout] no chunks arrived after tts.done");
        return;
      }
      pushTtsDebug("[audio.fallback.wait] tts.done received before chunks; retrying in 250ms");
      if (doneRetryTimerRef.current) window.clearTimeout(doneRetryTimerRef.current);
      doneRetryTimerRef.current = window.setTimeout(() => {
        doneRetryTimerRef.current = null;
        void markTtsDone();
      }, 250);
      return;
    }

    ttsDoneRetryCountRef.current = 0;
    const size = fallbackChunksRef.current.reduce((acc, c) => acc + c.byteLength, 0);
    if (ttsPlaybackLaunchedRef.current) {
      const grewEnough = size > ttsPlaybackBytesRef.current + 4096;
      const ended = !currentAudioRef.current || currentAudioRef.current.ended;
      if (!(grewEnough && ended)) {
        pushTtsDebug("[audio.fallback.skip] playback already launched for this utterance");
        return;
      }
      pushTtsDebug(`[audio.fallback.replay] relaunching with more audio bytes (old=${ttsPlaybackBytesRef.current}, new=${size})`);
    }

    // Primary playback path: play full utterance blob.
    ttsPlaybackLaunchedRef.current = true;
    ttsPlaybackBytesRef.current = size;
    pushTtsDebug(`[audio.fallback.prepare] chunks=${fallbackChunksRef.current.length} bytes=${size}`);
    const merged = new Uint8Array(size);
    let offset = 0;
    for (const c of fallbackChunksRef.current) {
      merged.set(c, offset);
      offset += c.byteLength;
    }
    const blob = new Blob([merged.buffer], { type: "audio/mpeg" });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    let playbackClosed = false;
    audio.onloadedmetadata = () => pushTtsDebug(`[audio.fallback.meta] duration=${audio.duration}`);
    audio.oncanplay = () => pushTtsDebug("[audio.fallback.canplay]");
    audio.onplaying = () => pushTtsDebug("[audio.fallback.playing]");
    audio.onerror = () => {
      if (playbackClosed || audio.ended) return;
      const mediaError = (audio as any).error;
      pushTtsDebug(`[audio.fallback.media_error] code=${mediaError?.code ?? "unknown"}`);
    };
    currentAudioRef.current = audio;
    try {
      await audio.play();
      pushTtsDebug("[audio.fallback.play] fallback blob playback started");
    } catch (e) {
      pushTtsDebug(`[audio.fallback.error] ${String((e as Error)?.message ?? e)}`);
    }
    audio.onended = () => {
      playbackClosed = true;
      pushTtsDebug("[audio.fallback.ended]");
      URL.revokeObjectURL(url);
    };
  }

  function stopPlayback() {
    appendQueueRef.current = [];
    fallbackChunksRef.current = [];
    ttsDoneRef.current = false;
    playbackStartedRef.current = false;
    ttsPlaybackLaunchedRef.current = false;
    ttsPlaybackBytesRef.current = 0;
    ttsDoneRetryCountRef.current = 0;
    activeTtsIdRef.current = null;
    if (doneRetryTimerRef.current) {
      window.clearTimeout(doneRetryTimerRef.current);
      doneRetryTimerRef.current = null;
    }

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
        appendTtsChunk(event.audioBase64, event.ttsId);
        break;
      case "tts.done":
        if (activeTtsIdRef.current === null) {
          activeTtsIdRef.current = event.ttsId;
          pushTtsDebug(`[tts.bind] tts.done bound active ttsId=${event.ttsId}`);
        }
        if (event.ttsId !== activeTtsIdRef.current) {
          pushTtsDebug(`[tts.ignore] stale done ttsId=${event.ttsId}, active=${activeTtsIdRef.current}`);
          break;
        }
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
    stopMicStreaming();
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
      void startMicStreaming().catch((e) => {
        setError(`mic_stream_error: ${String((e as Error)?.message ?? e)}`);
        pushTtsDebug(`[mic.error] ${String((e as Error)?.message ?? e)}`);
      });
    });
    client.connect();
  }

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    return () => {
      stopPlayback();
      stopMicStreaming();
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
        <pre style={{ background: "#0b1020", color: "#c7d2fe", padding: 10, borderRadius: 8, minHeight: 64, maxHeight: 220, overflowY: "auto" }}>
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
