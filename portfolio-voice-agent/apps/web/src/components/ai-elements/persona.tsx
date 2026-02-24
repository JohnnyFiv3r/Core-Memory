import { useEffect, useRef } from "react";
import type { VoiceState } from "../../lib/stateMachine";

type PersonaState = "idle" | "listening" | "thinking" | "speaking" | "asleep";
type PersonaVariant = "halo" | "opal" | "glint";

type PersonaProps = {
  state: PersonaState;
  variant?: PersonaVariant;
  className?: string;
  onReady?: () => void;
  onLoad?: () => void;
  onLoadError?: (error: Error) => void;
  onPlay?: () => void;
  onPause?: () => void;
  onStop?: () => void;
};

export function mapVoiceToPersonaState(state: VoiceState): PersonaState {
  if (state === "listening") return "listening";
  if (state === "thinking") return "thinking";
  if (state === "speaking") return "speaking";
  return "idle";
}

export function Persona({
  state,
  variant = "halo",
  className,
  onReady,
  onLoad,
  onLoadError,
  onPlay,
  onPause,
  onStop
}: PersonaProps) {
  const mountedRef = useRef(false);

  useEffect(() => {
    try {
      onLoad?.();
      onReady?.();
      mountedRef.current = true;
    } catch (e) {
      onLoadError?.(e as Error);
    }
    return () => {
      onStop?.();
    };
  }, [onLoad, onReady, onLoadError, onStop]);

  useEffect(() => {
    if (!mountedRef.current) return;
    if (state === "idle" || state === "asleep") onPause?.();
    else onPlay?.();
  }, [state, onPlay, onPause]);

  return (
    <div className={`orb-wrap persona-${variant} halo-state-${state} ${className ?? ""}`.trim()} aria-label={`Persona state: ${state}`}>
      <div className="halo-ring halo-ring-outer" />
      <div className="halo-ring halo-ring-mid" />
      <div className="halo-ring halo-ring-inner" />
      <div className="halo-core" />
    </div>
  );
}
