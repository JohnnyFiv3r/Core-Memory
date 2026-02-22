import type { VoiceState } from "../lib/stateMachine";

const stateStyle: Record<VoiceState, { ring: string; label: string }> = {
  idle: { ring: "#d4d4d8", label: "Idle" },
  requesting_mic: { ring: "#f59e0b", label: "Requesting mic" },
  gated: { ring: "#a78bfa", label: "Email gate" },
  connecting: { ring: "#38bdf8", label: "Connecting" },
  listening: { ring: "#22c55e", label: "Listening" },
  thinking: { ring: "#f97316", label: "Thinking" },
  speaking: { ring: "#0ea5e9", label: "Speaking" },
  error: { ring: "#ef4444", label: "Error" }
};

export function PersonaOrb({ state }: { state: VoiceState }) {
  const style = stateStyle[state];
  return (
    <div style={{ display: "grid", placeItems: "center", gap: 10 }}>
      <div
        style={{
          width: 140,
          height: 140,
          borderRadius: "50%",
          background: "radial-gradient(circle at 35% 30%, #f5f5f5, #a1a1aa)",
          boxShadow: `0 0 0 8px ${style.ring}33, 0 0 0 2px ${style.ring} inset`
        }}
      />
      <small style={{ color: "#52525b" }}>{style.label}</small>
    </div>
  );
}
