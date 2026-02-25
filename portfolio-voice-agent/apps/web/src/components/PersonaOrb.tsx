import type { VoiceState } from "../lib/stateMachine";
import { Persona, mapVoiceToPersonaState } from "./ai-elements/persona";

export function PersonaOrb({ state, className }: { state: VoiceState; className?: string }) {
  return <Persona className={className} state={mapVoiceToPersonaState(state)} variant="halo" />;
}
