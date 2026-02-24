import { useEffect, useMemo, useRef, useState } from "react";
import { HandIcon, MicIcon, Volume2Icon } from "./components/Icons";
import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";
import { nextState, type VoiceEvent, type VoiceState } from "./lib/stateMachine";
import { VoiceWsClient } from "./lib/wsClient";
import { PersonaOrb } from "./components/PersonaOrb";
import { TranscriptStrip } from "./components/TranscriptStrip";

const DEFAULT_WS_URL = (import.meta.env.VITE_VOICE_SERVER_WS_URL as string) ?? "ws://localhost:8787";

function wsToHttp(wsUrl: string): string {
  if (wsUrl.startsWith("wss://")) return wsUrl.replace("wss://", "https://");
  if (wsUrl.startsWith("ws://")) return wsUrl.replace("ws://", "http://");
  return wsUrl;
}

const DEFAULT_HTTP_URL = (import.meta.env.VITE_VOICE_SERVER_HTTP_URL as string) ?? wsToHttp(DEFAULT_WS_URL);

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function App() {
  const [state, setState] = useState<VoiceState>("idle");
  const [conversationActive, setConversationActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [history, setHistory] = useState<string[]>(["idle"]);
  const [transcript, setTranscript] = useState<string[]>([]);
  const [actionChips, setActionChips] = useState<Array<{ label: string; slug?: string }>>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [ttsDebug, setTtsDebug] = useState<string[]>([]);
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [copiedEmail, setCopiedEmail] = useState(false);
  const [webflowAuthStatus, setWebflowAuthStatus] = useState<"idle" | "exchanging" | "received" | "missing" | "error">("idle");

  const stateRef = useRef<VoiceState>("idle");
  const userStoppingRef = useRef(false);

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
  const buttonLabel = conversationActive ? "Stop conversation" : "Start talking";
  const contactEmail = (import.meta.env.VITE_CONTACT_EMAIL as string) || "john@wristchat.net";
  const isEmbed = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("embed") === "1";
  const featuredProjects = [
    {
      title: "Storyboard",
      description: "AI-driven hands-free voice interface for truck-driver and dispatcher communication.",
      tags: ["Voice UX", "Accessibility", "Beta Launch"],
      image: "https://images.unsplash.com/photo-1464219789935-c2d9d9aba644?auto=format&fit=crop&w=1200&q=80"
    },
    {
      title: "Midwest Muscle Nutrition",
      description: "Co-founded consumer brand with product strategy, packaging, and go-to-market execution.",
      tags: ["Brand", "Consumer", "Growth"],
      image: "https://images.unsplash.com/photo-1579722821273-0f6c38d1f3f0?auto=format&fit=crop&w=1200&q=80"
    }
  ];

  const experience = [
    {
      company: "Storyboard",
      role: "Director of Design",
      period: "Aug 2021 — Today",
      blurb: "Led design for a voice-first trucking communication product, from research to pilot rollout."
    },
    {
      company: "Midwest Muscle Nutrition",
      role: "Co-Founder",
      period: "May 2021 — Today",
      blurb: "Built brand and product narrative around an ultra-high protein bar concept."
    },
    {
      company: "Contour Airlines",
      role: "Manager, Marketing & Distribution",
      period: "Nov 2015 — Aug 2021",
      blurb: "Owned digital product and ticket distribution with cross-functional airline initiatives."
    }
  ];

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
        setConversationActive(true);
        userStoppingRef.current = false;
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
        // Keep persona in "thinking" while TTS audio is being generated.
        apply("ASSISTANT_THINKING");
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
        // Transition to speaking as soon as we receive actual audio payload.
        if (stateRef.current !== "speaking") apply("ASSISTANT_SPEAKING");
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
        setWsConnected(false);
        setConversationActive(false);
        stopMicStreaming();
        stopPlayback();
        if (event.reason === "user") {
          apply("END");
        } else if (event.reason === "limit") {
          setError("Session time limit reached. Tap Start talking to begin a new conversation.");
          apply("FAIL");
        } else {
          setError("Session ended unexpectedly.");
          apply("FAIL");
        }
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

  async function copyEmailToClipboard() {
    try {
      await navigator.clipboard.writeText(contactEmail);
      setCopiedEmail(true);
      window.setTimeout(() => setCopiedEmail(false), 1400);
    } catch {
      setError("Could not copy email. You can still use it manually below.");
    }
  }

  async function handleToggle() {
    if (!canToggle) return;

    if (!conversationActive) {
      await requestMic();
      return;
    }

    userStoppingRef.current = true;
    stopPlayback();
    stopMicStreaming();
    send({ type: "session.stop", reason: "user" });
    wsRef.current?.close();
    wsRef.current = null;
    setWsConnected(false);
    setConversationActive(false);
    apply("END");
  }

  async function submitEmail() {
    if (!email.includes("@")) {
      setError("Enter a valid email to continue");
      return;
    }

    // Email verification click is a direct user gesture: piggyback audio unlock here.
    await unlockAudioOutput();

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
    client.onClose(() => {
      setWsConnected(false);
      stopMicStreaming();
      if (!userStoppingRef.current) {
        setConversationActive(false);
        setError("Connection closed. Tap Start talking to reconnect.");
        apply("FAIL");
      }
    });
    client.connect();
  }

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const path = window.location.pathname;
    if (!path.startsWith("/webflow/oauth/callback")) return;

    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");

    if (!code) {
      setWebflowAuthStatus("missing");
      return;
    }

    // Keep code out of the visible URL immediately.
    sessionStorage.setItem("webflow_oauth_code", code);
    window.history.replaceState({}, document.title, "/webflow/oauth/callback");
    setWebflowAuthStatus("exchanging");

    fetch(`${DEFAULT_HTTP_URL}/oauth/webflow/exchange?code=${encodeURIComponent(code)}`)
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `exchange_failed_${res.status}`);
        }
        return res.json();
      })
      .then(() => {
        setWebflowAuthStatus("received");
      })
      .catch((err) => {
        setWebflowAuthStatus("error");
        setError(`webflow_oauth_exchange_failed: ${String((err as Error)?.message ?? err)}`);
      });
  }, []);

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
      conversationActive,
      hasMic: conversationActive,
      isGated: state === "gated",
      email,
      wsUrl: DEFAULT_WS_URL,
      wsConnected,
      transitions: history,
      transcriptTail: transcript.slice(-6),
      ttsDebugTail: ttsDebug.slice(-8)
    }),
    [state, canToggle, conversationActive, email, history, transcript, wsConnected, ttsDebug]
  );

  if (isEmbed) {
    return (
      <main className="ji-embed-shell">
        <div className="ji-embed-card">
          <PersonaOrb state={state} />
          <button className="mic-btn" onClick={handleToggle} disabled={!canToggle} aria-label={buttonLabel}>
            <MicIcon size={24} />
          </button>
          <p className="hero-copy">Talk to my portfolio voice agent.</p>
          <div className="status-row">
            <span className="status-chip">{state}</span>
            <span className="status-chip">{wsConnected ? "connected" : "disconnected"}</span>
          </div>
          <div className="hero-actions">
            <button onClick={unlockAudioOutput} className="text-btn icon-btn"><Volume2Icon size={14} /> {audioUnlocked ? "Audio unlocked" : "Unlock audio"}</button>
            {conversationActive && <button onClick={() => { stopPlayback(); send({ type: "voice.interrupt" }); }} className="text-btn icon-btn"><HandIcon size={14} /> Interrupt</button>}
          </div>
          {state === "gated" && (
            <div className="email-gate">
              <input type="email" className="field" placeholder="recruiter@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              <button onClick={submitEmail} className="text-btn">Verify + Connect</button>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      </main>
    );
  }

  return (
    <main className="portfolio-shell">
      <header className="top-nav">
        <div className="logo-box">JI</div>
        <nav className="nav-links">
          <a href="#projects" className="nav-pill nav-pill-active">Projects</a>
          <a href="#about" className="nav-pill">About</a>
          <a href="#contact" className="nav-pill">Contact</a>
        </nav>
        <button className="copy-btn" onClick={copyEmailToClipboard}>{copiedEmail ? "Copied" : "Copy Email"}</button>
      </header>

      {webflowAuthStatus !== "idle" && (
        <section className="oauth-notice" role="status">
          {webflowAuthStatus === "exchanging" && "Webflow auth code captured. Exchanging for access token..."}
          {webflowAuthStatus === "received" && "Webflow auth complete. Token saved server-side and code removed from URL."}
          {webflowAuthStatus === "missing" && "Webflow callback loaded without a code parameter."}
          {webflowAuthStatus === "error" && "Webflow OAuth exchange failed. Check server logs and env credentials."}
        </section>
      )}

      <section className="hero-center" id="home">
        <p className="kicker">Product Designer + Voice AI Builder</p>
        <h1 className="hero-title">I design and ship conversational product experiences.</h1>

        <PersonaOrb state={state} />

        <div className="mic-wrap">
          <button className="mic-btn" onClick={handleToggle} disabled={!canToggle} aria-label={buttonLabel}>
            <MicIcon size={24} />
          </button>
          <p className="hero-copy">Tap the microphone to talk to my portfolio agent about projects, decisions, and results.</p>
          <div className="status-row">
            <span className="status-chip">State: {state}</span>
            <span className="status-chip">WS: {wsConnected ? "connected" : "disconnected"}</span>
            <span className="status-chip">Mic: {conversationActive ? "live" : "idle"}</span>
          </div>
          <div className="hero-actions">
            <button onClick={unlockAudioOutput} className="text-btn icon-btn">
              <Volume2Icon size={14} /> {audioUnlocked ? "Audio unlocked" : "Unlock audio"}
            </button>
            {conversationActive && (
              <button
                onClick={() => {
                  stopPlayback();
                  send({ type: "voice.interrupt" });
                }}
                className="text-btn icon-btn"
              >
                <HandIcon size={14} /> Interrupt assistant
              </button>
            )}
          </div>
        </div>

        {state === "gated" && (
          <div className="email-gate">
            <input
              type="email"
              className="field"
              placeholder="recruiter@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <button onClick={submitEmail} className="text-btn">Verify Email + Connect</button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      <section id="projects" className="featured-section">
        <h2>Featured Work—</h2>
        <div className="project-grid">
          {featuredProjects.map((project) => (
            <article className="project-card" key={project.title}>
              <img src={project.image} alt={`${project.title} visual`} />
              <div className="project-overlay">
                <h3>{project.title}</h3>
                <p>{project.description}</p>
                <div className="tag-row">
                  {project.tags.map((tag) => (
                    <span className="tag" key={tag}>{tag}</span>
                  ))}
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="experience-section" id="about">
        <h2>Experience</h2>
        <div className="experience-list">
          {experience.map((item) => (
            <article key={`${item.company}-${item.role}`} className="experience-item">
              <div>
                <h3>{item.company}</h3>
                <p className="exp-role">{item.role}</p>
              </div>
              <p className="exp-period">{item.period}</p>
              <p className="exp-blurb">{item.blurb}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="case-study" id="storyboard">
        <h2>Storyboard — Beating Isolation on the Road</h2>
        <p>
          We pivoted from podcasting to a voice-first team communication model. I led discovery interviews, journey mapping,
          hands-free prototype testing, and public beta delivery for trucking customers.
        </p>
        <div className="case-grid">
          <div><h4>Discovery</h4><p>Interviewed drivers and dispatchers to map communication breakdowns and accessibility constraints.</p></div>
          <div><h4>Strategy</h4><p>Prioritized voice commands, transcription, and translation for high-frequency, low-attention workflows.</p></div>
          <div><h4>Testing</h4><p>Ran real-world usability tests and refined recognition reliability with redundant audio/visual messaging.</p></div>
          <div><h4>Impact</h4><p>Reduced design delivery time using fast sprint loops; launched pilots with Grand Island Express, Cypress, and Ryder.</p></div>
        </div>
      </section>

      <section className="live-panels">
        <div className="card-light">
          <TranscriptStrip lines={transcript} />
        </div>
        <div className="card-light" id="contact">
          <h3 className="section-title-dark">Suggested Actions</h3>
          <div className="pill-row">
            {actionChips.length === 0 ? (
              <small className="mini-dark">No project suggestions yet.</small>
            ) : (
              actionChips.map((chip, idx) => (
                <button
                  key={`${chip.label}-${idx}`}
                  className="pill-light"
                  onClick={() => setTranscript((t) => [...t, `ui-action: ${chip.slug ?? chip.label}`])}
                >
                  {chip.label}
                </button>
              ))
            )}
          </div>
          <p className="contact-copy">{contactEmail}</p>
        </div>
      </section>

      <section className="card-light debug-wrap">
        <div className="debug-head">
          <h3 className="section-title-dark">Developer Debug</h3>
          <button className="text-btn" onClick={() => setShowDebug((v) => !v)}>{showDebug ? "Hide debug" : "Show debug"}</button>
        </div>
        {showDebug ? (
          <div className="debug-grid">
            <pre className="log">{ttsDebug.length ? ttsDebug.join("\n") : "No TTS debug events yet."}</pre>
            <pre className="debug">{JSON.stringify(debug, null, 2)}</pre>
          </div>
        ) : (
          <p className="mini-dark">Debug panels are hidden.</p>
        )}
      </section>
    </main>
  );
}
