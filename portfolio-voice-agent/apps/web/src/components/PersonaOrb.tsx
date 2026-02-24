import type { VoiceState } from "../lib/stateMachine";

export function PersonaOrb({ state }: { state: VoiceState }) {
  return (
    <div className={`orb-wrap halo halo-${state}`} aria-label={`Persona state: ${state}`}>
      <div className="halo-ring halo-ring-outer" />
      <div className="halo-ring halo-ring-inner" />
      <div className="halo-core" />
    </div>
  );
}
