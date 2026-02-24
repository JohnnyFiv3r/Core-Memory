import type { VoiceState } from "../lib/stateMachine";

const stateStyle: Record<VoiceState, { ring: string; label: string; glow: string }> = {
  idle: { ring: "#334155", label: "Idle", glow: "rgba(148,163,184,.18)" },
  requesting_mic: { ring: "#f59e0b", label: "Requesting mic", glow: "rgba(245,158,11,.25)" },
  gated: { ring: "#a78bfa", label: "Email gate", glow: "rgba(167,139,250,.25)" },
  connecting: { ring: "#38bdf8", label: "Connecting", glow: "rgba(56,189,248,.24)" },
  listening: { ring: "#22c55e", label: "Listening", glow: "rgba(34,197,94,.24)" },
  thinking: { ring: "#f97316", label: "Thinking", glow: "rgba(249,115,22,.24)" },
  speaking: { ring: "#06b6d4", label: "Speaking", glow: "rgba(6,182,212,.24)" },
  error: { ring: "#ef4444", label: "Error", glow: "rgba(239,68,68,.24)" }
};

export function PersonaOrb({ state }: { state: VoiceState }) {
  const style = stateStyle[state];
  return (
    <div className="orb-wrap">
      <div
        style={{
          width: 150,
          height: 150,
          borderRadius: "50%",
          background: "radial-gradient(circle at 35% 30%, #e2e8f0, #0f172a 65%)",
          boxShadow: `0 0 0 8px ${style.glow}, 0 0 0 2px ${style.ring} inset, 0 20px 50px -25px ${style.ring}`
        }}
      />
      <small className="orb-label">{style.label}</small>
    </div>
  );
}
