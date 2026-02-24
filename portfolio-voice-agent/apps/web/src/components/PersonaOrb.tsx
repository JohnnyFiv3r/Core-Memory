import type { VoiceState } from "../lib/stateMachine";

const stateStyle: Record<VoiceState, { label: string }> = {
  idle: { label: "Idle" },
  requesting_mic: { label: "Requesting mic" },
  gated: { label: "Email gate" },
  connecting: { label: "Connecting" },
  listening: { label: "Listening" },
  thinking: { label: "Thinking" },
  speaking: { label: "Speaking" },
  error: { label: "Error" }
};

export function PersonaOrb({ state }: { state: VoiceState }) {
  const style = stateStyle[state];

  return (
    <div className={`orb-wrap halo halo-${state}`}>
      <div className="halo-ring halo-ring-outer" />
      <div className="halo-ring halo-ring-inner" />
      <div className="halo-core" />
      <small className="orb-label">{style.label}</small>
    </div>
  );
}
