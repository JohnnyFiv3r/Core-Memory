import type { VoiceState } from "../lib/stateMachine";
import type { PersonaState } from "./ai-elements/persona";
import { Persona } from "./ai-elements/persona";

function mapVoiceToPersonaState(state: VoiceState): PersonaState {
  if (state === "listening") return "listening";
  if (state === "thinking") return "thinking";
  if (state === "speaking") return "speaking";
  return "idle";
}

export function PersonaOrb({ state, className }: { state: VoiceState; className?: string }) {
  return <Persona className={className} state={mapVoiceToPersonaState(state)} variant="halo" />;
}
