import { useMemo, useState } from "react";
import { nextState, type VoiceEvent, type VoiceState } from "./lib/stateMachine";

const MOCK_EMAIL = "recruiter@example.com";

export function App() {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [history, setHistory] = useState<string[]>(["idle"]);

  const canToggle = state !== "requesting_mic" && state !== "connecting";
  const buttonLabel = state === "idle" || state === "error" ? "Start conversation" : "End conversation";

  function apply(event: VoiceEvent) {
    setState((prev) => {
      const next = nextState(prev, event);
      if (next !== prev) setHistory((h) => [...h, next]);
      return next;
    });
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

    apply("END");
  }

  function submitEmail() {
    if (!email.includes("@")) {
      setError("Enter a valid email to continue");
      return;
    }
    setError(null);
    apply("EMAIL_VERIFIED");
    setTimeout(() => apply("CONNECTED"), 300);
  }

  const debug = useMemo(
    () => ({
      state,
      canToggle,
      hasMic: state !== "idle",
      isGated: state === "gated",
      email,
      mockAcceptedEmail: MOCK_EMAIL,
      transitions: history
    }),
    [state, canToggle, email, history]
  );

  return (
    <main style={{ fontFamily: "Inter, system-ui, sans-serif", maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <h1>Portfolio Voice Agent — Scaffold</h1>
      <p>Beads B-001..B-003: mic permission + session state machine + debug visibility.</p>

      <section style={{ marginTop: 24, display: "flex", gap: 12, alignItems: "center" }}>
        <button onClick={handleToggle} disabled={!canToggle} style={{ padding: "10px 16px", borderRadius: 8 }}>
          {buttonLabel}
        </button>
        <span><strong>State:</strong> {state}</span>
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
          <button onClick={submitEmail} style={{ padding: "8px 12px" }}>Verify Email</button>
        </section>
      )}

      {state === "listening" && (
        <section style={{ marginTop: 16, display: "flex", gap: 8 }}>
          <button onClick={() => apply("ASSISTANT_THINKING")} style={{ padding: "8px 10px" }}>Simulate assistant thinking</button>
        </section>
      )}

      {state === "thinking" && (
        <section style={{ marginTop: 16, display: "flex", gap: 8 }}>
          <button onClick={() => apply("ASSISTANT_SPEAKING")} style={{ padding: "8px 10px" }}>Simulate assistant speaking</button>
        </section>
      )}

      {state === "speaking" && (
        <section style={{ marginTop: 16, display: "flex", gap: 8 }}>
          <button onClick={() => apply("END_SPEAKING")} style={{ padding: "8px 10px" }}>Simulate end speaking</button>
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
