import { jsx as _jsx } from "react/jsx-runtime";
import { Persona, mapVoiceToPersonaState } from "./ai-elements/persona";
export function PersonaOrb({ state }) {
    return _jsx(Persona, { state: mapVoiceToPersonaState(state), variant: "halo" });
}
