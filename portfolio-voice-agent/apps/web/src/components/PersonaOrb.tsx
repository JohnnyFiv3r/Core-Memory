import type { VoiceState } from "../lib/stateMachine";
import { Persona, mapVoiceToPersonaState } from "./ai-elements/persona";

export function PersonaOrb({ state }: { state: VoiceState }) {
  return <Persona state={mapVoiceToPersonaState(state)} variant="halo" />;
}
