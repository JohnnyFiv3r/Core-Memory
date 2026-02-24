import type { VoiceState } from "../lib/stateMachine";

type HaloPersonaState = "idle" | "listening" | "thinking" | "speaking";

function toHaloState(state: VoiceState): HaloPersonaState {
  if (state === "listening") return "listening";
  if (state === "thinking") return "thinking";
  if (state === "speaking") return "speaking";
  return "idle";
}

export function PersonaOrb({ state }: { state: VoiceState }) {
  const haloState = toHaloState(state);

  return (
    <div className={`orb-wrap halo halo-state-${haloState}`} aria-label={`Persona state: ${haloState}`}>
      <div className="halo-ring halo-ring-outer" />
      <div className="halo-ring halo-ring-mid" />
      <div className="halo-ring halo-ring-inner" />
      <div className="halo-core" />
    </div>
  );
}
