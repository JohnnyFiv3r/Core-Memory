import { useEffect, useMemo, useRef, useState } from "react";
import { HandIcon, MicIcon, Volume2Icon } from "./components/Icons";
import type { ClientEvent, ServerEvent } from "@portfolio/shared-types";
import { nextState, type VoiceEvent, type VoiceState } from "./lib/stateMachine";
import { VoiceWsClient } from "./lib/wsClient";
import { PersonaOrb } from "./components/PersonaOrb";
import { TranscriptStrip } from "./components/TranscriptStrip";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

function wsToHttp(wsUrl: string): string {
  if (wsUrl.startsWith("wss://")) return wsUrl.replace("wss://", "https://");
  if (wsUrl.startsWith("ws://")) return wsUrl.replace("ws://", "http://");
  return wsUrl;
}

function resolveDefaultWsUrl(): string {
  const envWs = import.meta.env.VITE_VOICE_SERVER_WS_URL as string | undefined;
  if (envWs) return envWs;

  if (typeof window !== "undefined") {
    const wsParam = new URLSearchParams(window.location.search).get("ws");
    if (wsParam) return wsParam;
  }

  return "ws://localhost:8787";
}

const DEFAULT_WS_URL = resolveDefaultWsUrl();
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
  const iosAudioRef = useRef<HTMLAudioElement | null>(null);
  const ttsQueueRef = useRef<Uint8Array[][]>([]);
  const ttsQueuePlayingRef = useRef(false);

  const micStreamRef = useRef<MediaStream | null>(null);
  const micContextRef = useRef<AudioContext | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const micSeqRef = useRef(0);

  const canToggle = state !== "requesting_mic" && state !== "connecting";
  const buttonLabel = conversationActive ? "Stop conversation" : "Start talking";
  const contactEmail = (import.meta.env.VITE_CONTACT_EMAIL as string) || "john@wristchat.net";
  const isEmbed =
    typeof window !== "undefined" &&
    (new URLSearchParams(window.location.search).get("embed") === "1" ||
      window.location.pathname === "/embed" ||
      window.self !== window.top);

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
    if (audioContext.state !== "running") {
      await audioContext.resume();
    }
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

  function startNewTtsStream() {
    // Don't kill current playback — just reset chunk collection for this segment
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

    ttsPlaybackLaunchedRef.current = true;
    ttsPlaybackBytesRef.current = size;
    const merged = new Uint8Array(size);
    let offset = 0;
    for (const c of fallbackChunksRef.current) {
      merged.set(c, offset);
      offset += c.byteLength;
    }

    // Enqueue this segment's audio and play sequentially
    ttsQueueRef.current.push([merged]);
    pushTtsDebug(`[audio.queue] enqueued segment (${size} bytes), queue depth=${ttsQueueRef.current.length}`);
    void playNextInQueue();
  }

  async function playNextInQueue() {
    if (ttsQueuePlayingRef.current) return;
    const next = ttsQueueRef.current.shift();
    if (!next || next.length === 0) {
      // Queue empty — done speaking
      apply("END_SPEAKING");
      return;
    }
    ttsQueuePlayingRef.current = true;
    const totalSize = next.reduce((a, c) => a + c.byteLength, 0);
    const merged = new Uint8Array(totalSize);
    let off = 0;
    for (const c of next) { merged.set(c, off); off += c.byteLength; }
    const blob = new Blob([merged.buffer], { type: "audio/mpeg" });
    const url = URL.createObjectURL(blob);

    const audio = iosAudioRef.current ?? new Audio();
    audio.onended = null;
    audio.onerror = null;
    audio.src = url;
    audio.muted = false;
    let playbackClosed = false;
    currentAudioRef.current = audio;
    try {
      await audio.play();
      pushTtsDebug(`[audio.queue.play] playing segment (${totalSize} bytes), remaining=${ttsQueueRef.current.length}`);
      apply("ASSISTANT_SPEAKING");
    } catch (e) {
      pushTtsDebug(`[audio.queue.error] ${String((e as Error)?.message ?? e)}`);
      ttsQueuePlayingRef.current = false;
      apply("END_SPEAKING");
      return;
    }
    audio.onended = () => {
      playbackClosed = true;
      pushTtsDebug("[audio.queue.ended]");
      URL.revokeObjectURL(url);
      ttsQueuePlayingRef.current = false;
      void playNextInQueue();
    };
    audio.onerror = () => {
      if (playbackClosed || audio.ended) return;
      const mediaError = (audio as any).error;
      pushTtsDebug(`[audio.queue.media_error] code=${mediaError?.code ?? "unknown"}`);
      ttsQueuePlayingRef.current = false;
      void playNextInQueue();
    };
  }

  function stopPlayback() {
    appendQueueRef.current = [];
    fallbackChunksRef.current = [];
    ttsQueueRef.current = [];
    ttsQueuePlayingRef.current = false;
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
      } catch {}
    }
    if (mediaSourceRef.current?.readyState === "open") {
      try {
        mediaSourceRef.current.endOfStream();
      } catch {}
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
        break;
      case "assistant.text.final":
        setTranscript((t) => [...t, `assistant: ${event.text}`]);
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
        break;
      case "debug.tts":
        pushTtsDebug(`[${event.stage}] ${event.detail ?? ""}`.trim());
        break;
      case "error":
        // TTS stream aborts are expected when a new segment starts — don't kill session
        if (event.code === "tts_stream_failed" && event.message?.includes("aborted")) {
          pushTtsDebug(`[tts.abort] non-fatal: ${event.message}`);
          break;
        }
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
      if (!Ctx) return;
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

      // Prime a persistent Audio element during user gesture for iOS Safari.
      // iOS requires play() to originate from a user action; once primed, the
      // element can be reused for subsequent src changes without gesture.
      if (!iosAudioRef.current) {
        const a = new Audio();
        a.muted = true;
        (a as any).playsInline = true;
        a.setAttribute("playsinline", "");
        // Create a tiny silent mp3 frame to prime with
        const silentMp3 = "data:audio/mpeg;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYYK";
        a.src = silentMp3;
        try { await a.play(); } catch { /* expected on some browsers */ }
        a.muted = false;
        a.pause();
        iosAudioRef.current = a;
        pushTtsDebug("[audio.unlock] iOS persistent audio element primed");
      }

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

    await unlockAudioOutput();

    // Fire-and-forget lead capture to Google Sheet
    try {
      fetch("https://script.google.com/macros/s/AKfycbz_56rmBIUdJZvCHKthCzyHyOoc_bZylPo0c4xq0BFavBK-Hvu_fr9YEYS9vKBMuoH9Ig/exec", {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({
          email,
          source: isEmbed ? "embed" : "standalone",
          userAgent: navigator.userAgent
        })
      });
    } catch { /* don't block voice session on lead capture failure */ }

    setError(null);
    apply("EMAIL_VERIFIED");

    let sessionReady = false;
    const client = new VoiceWsClient(DEFAULT_WS_URL, (event) => {
      if (event.type === "session.ready") sessionReady = true;
      onServerEvent(event);
    });
    wsRef.current = client;
    let opened = false;
    const connectTimer = window.setTimeout(() => {
      if (!opened) {
        setError(`Connection timeout reaching voice server (${DEFAULT_WS_URL}).`);
        apply("FAIL");
      }
    }, 8000);

    client.onOpen(() => {
      opened = true;
      window.clearTimeout(connectTimer);
      wsRef.current?.send({ type: "session.start", email });
      void startMicStreaming().catch((e) => {
        setError(`mic_stream_error: ${String((e as Error)?.message ?? e)}`);
      });
    });
    client.onError(() => {
      setError(`Could not open voice websocket (${DEFAULT_WS_URL}). Verify endpoint/certs from this device.`);
      apply("FAIL");
    });
    client.onClose((evt) => {
      window.clearTimeout(connectTimer);
      setWsConnected(false);
      stopMicStreaming();
      if (!userStoppingRef.current) {
        setConversationActive(false);
        const details = `code=${evt.code}${evt.reason ? ` reason=${evt.reason}` : ""}`;
        if (!sessionReady) {
          setError(`Voice websocket closed before session init (${details}) at ${DEFAULT_WS_URL}.`);
        } else {
          setError("Connection closed. Tap Start talking to reconnect.");
        }
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
      .then(() => setWebflowAuthStatus("received"))
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

  useEffect(() => {
    if (!isEmbed) return;
    const prevBodyBg = document.body.style.background;
    const prevHtmlBg = document.documentElement.style.background;
    document.body.style.background = "transparent";
    document.documentElement.style.background = "transparent";
    return () => {
      document.body.style.background = prevBodyBg;
      document.documentElement.style.background = prevHtmlBg;
    };
  }, [isEmbed]);

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

  const showGate = state === "gated";

  if (isEmbed) {
    return (
      <main className="relative grid h-full w-full place-items-center overflow-hidden bg-transparent p-2 text-foreground">
        <div className="grid w-full max-w-sm justify-items-center gap-1.5">
          <div
            className="relative grid place-items-center"
            style={{ height: "min(56vh, 360px)", width: "min(90vw, 360px)" }}
          >
            <PersonaOrb className="origin-center scale-[1.7] max-sm:scale-[1.5]" state={state} />
            <Button className="absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full" onClick={handleToggle} disabled={!canToggle} aria-label={buttonLabel}>
              <MicIcon size={24} />
            </Button>
          </div>
          <p className="max-w-sm text-center text-sm leading-tight text-muted-foreground">Tap the microphone to talk to my portfolio agent about projects, decisions, and results.</p>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        {showGate && (
          <Card className="absolute left-1/2 top-6 z-20 w-[min(92vw,22rem)] -translate-x-1/2 border-border/80 bg-card/95">
            <CardHeader>
              <CardTitle className="text-lg">Continue with email</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              <Input type="email" placeholder="recruiter@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              <Button onClick={submitEmail}>Verify Email + Continue</Button>
            </CardContent>
          </Card>
        )}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl space-y-10 bg-background px-4 py-8 text-foreground sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border/70 bg-card/30 p-4">
        <div className="grid h-14 w-14 place-items-center rounded-md bg-primary text-primary-foreground font-bold tracking-[0.08em]">JI</div>
        <nav className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => document.getElementById("projects")?.scrollIntoView({ behavior: "smooth" })}>Projects</Button>
          <Button variant="outline" size="sm" onClick={() => document.getElementById("about")?.scrollIntoView({ behavior: "smooth" })}>About</Button>
          <Button variant="outline" size="sm" onClick={() => document.getElementById("contact")?.scrollIntoView({ behavior: "smooth" })}>Contact</Button>
        </nav>
        <Button onClick={copyEmailToClipboard}>{copiedEmail ? "Copied" : "Copy Email"}</Button>
      </header>

      {webflowAuthStatus !== "idle" && (
        <Card className="border-blue-200 bg-blue-50 text-slate-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-100">
          <CardContent className="p-4 text-sm">
            {webflowAuthStatus === "exchanging" && "Webflow auth code captured. Exchanging for access token..."}
            {webflowAuthStatus === "received" && "Webflow auth complete. Token saved server-side and code removed from URL."}
            {webflowAuthStatus === "missing" && "Webflow callback loaded without a code parameter."}
            {webflowAuthStatus === "error" && "Webflow OAuth exchange failed. Check server logs and env credentials."}
          </CardContent>
        </Card>
      )}

      <section className="space-y-5 text-center">
        <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Product Designer + Voice AI Builder</p>
        <h1 className="mx-auto max-w-4xl text-balance text-4xl font-semibold tracking-tight sm:text-6xl">I design and ship conversational product experiences.</h1>

        <div className="relative mx-auto grid h-[820px] w-[820px] place-items-center max-sm:h-[460px] max-sm:w-[460px]">
          <PersonaOrb className="origin-center scale-[4] max-sm:scale-[2.2]" state={state} />
          <Button className="absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full" onClick={handleToggle} disabled={!canToggle} aria-label={buttonLabel}>
            <MicIcon size={24} />
          </Button>
        </div>

        <p className="mx-auto max-w-md text-muted-foreground">Tap the microphone to talk to my portfolio agent about projects, decisions, and results.</p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Badge>State: {state}</Badge>
          <Badge>WS: {wsConnected ? "connected" : "disconnected"}</Badge>
          <Badge>Mic: {conversationActive ? "live" : "idle"}</Badge>
        </div>

        <div className="flex flex-wrap justify-center gap-2">
          <Button variant="outline" size="sm" onClick={unlockAudioOutput} className="gap-2">
            <Volume2Icon size={14} /> {audioUnlocked ? "Audio unlocked" : "Unlock audio"}
          </Button>
          {conversationActive && (
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() => {
                stopPlayback();
                send({ type: "voice.interrupt" });
              }}
            >
              <HandIcon size={14} /> Interrupt assistant
            </Button>
          )}
        </div>

        {showGate && (
          <Card className="mx-auto w-full max-w-xl border-border/80 bg-card/70">
            <CardContent className="grid gap-3 p-4 sm:grid-cols-[1fr_auto]">
              <Input
                type="email"
                placeholder="recruiter@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <Button onClick={submitEmail}>Verify Email + Connect</Button>
            </CardContent>
          </Card>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}
      </section>

      <section id="projects" className="space-y-4">
        <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">Featured Work</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {featuredProjects.map((project) => (
            <Card key={project.title} className="group overflow-hidden border-border/70 bg-card/50">
              <div className="relative">
                <img src={project.image} alt={`${project.title} visual`} className="h-64 w-full object-cover" />
                <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/40 to-transparent p-4 text-white">
                  <h3 className="text-lg font-semibold">{project.title}</h3>
                  <p className="text-sm text-white/90">{project.description}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {project.tags.map((tag) => (
                      <Badge key={tag} className="border-white/40 bg-white/10 text-white">{tag}</Badge>
                    ))}
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </section>

      <section className="space-y-4" id="about">
        <h2 className="text-3xl font-semibold tracking-tight">Experience</h2>
        <div className="grid gap-3">
          {experience.map((item) => (
            <Card key={`${item.company}-${item.role}`} className="border-border/70 bg-card/40">
              <CardContent className="space-y-2 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-lg font-semibold">{item.company}</h3>
                  <span className="text-xs text-muted-foreground">{item.period}</span>
                </div>
                <p className="text-sm font-medium text-muted-foreground">{item.role}</p>
                <p className="text-sm text-foreground/90">{item.blurb}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-3xl font-semibold tracking-tight">Storyboard — Beating Isolation on the Road</h2>
        <p className="max-w-4xl text-muted-foreground">
          We pivoted from podcasting to a voice-first team communication model. I led discovery interviews, journey mapping,
          hands-free prototype testing, and public beta delivery for trucking customers.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          {[
            ["Discovery", "Interviewed drivers and dispatchers to map communication breakdowns and accessibility constraints."],
            ["Strategy", "Prioritized voice commands, transcription, and translation for high-frequency, low-attention workflows."],
            ["Testing", "Ran real-world usability tests and refined recognition reliability with redundant audio/visual messaging."],
            ["Impact", "Reduced design delivery time using fast sprint loops; launched pilots with Grand Island Express, Cypress, and Ryder."],
          ].map(([title, copy]) => (
            <Card key={title} className="border-border/70 bg-card/40">
              <CardContent className="p-4">
                <h4 className="font-semibold">{title}</h4>
                <p className="mt-1 text-sm text-muted-foreground">{copy}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <TranscriptStrip lines={transcript} />

        <Card className="border-border/80 bg-card/40" id="contact">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm uppercase tracking-widest text-muted-foreground">Suggested Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {actionChips.length === 0 ? (
                <small className="text-muted-foreground">No project suggestions yet.</small>
              ) : (
                actionChips.map((chip, idx) => (
                  <Button
                    key={`${chip.label}-${idx}`}
                    variant="outline"
                    size="sm"
                    onClick={() => setTranscript((t) => [...t, `ui-action: ${chip.slug ?? chip.label}`])}
                  >
                    {chip.label}
                  </Button>
                ))
              )}
            </div>
            <p className="font-medium text-foreground/90">{contactEmail}</p>
          </CardContent>
        </Card>
      </section>

      <Card className="border-border/80 bg-card/40">
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-sm uppercase tracking-widest text-muted-foreground">Developer Debug</CardTitle>
          <Button variant="outline" size="sm" onClick={() => setShowDebug((v) => !v)}>{showDebug ? "Hide debug" : "Show debug"}</Button>
        </CardHeader>
        <CardContent>
          {showDebug ? (
            <div className="grid gap-3 md:grid-cols-2">
              <pre className="max-h-64 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-blue-100">{ttsDebug.length ? ttsDebug.join("\n") : "No TTS debug events yet."}</pre>
              <pre className="max-h-64 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-blue-100">{JSON.stringify(debug, null, 2)}</pre>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Debug panels are hidden.</p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
