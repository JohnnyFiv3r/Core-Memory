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
    return () => onStop?.();
  }, [onLoad, onReady, onLoadError, onStop]);

  useEffect(() => {
    if (!mountedRef.current) return;
    if (state === "idle" || state === "asleep") onPause?.();
    else onPlay?.();
  }, [state, onPlay, onPause]);

  return (
    <div className={`persona persona-${variant} persona-state-${state} ${className ?? ""}`.trim()} aria-label={`Persona state: ${state}`}>
      <div className="persona-core" />

      <div className="persona-listening-rings" aria-hidden="true">
        <div className="ring ring-1" />
        <div className="ring ring-2" />
        <div className="ring ring-3" />
      </div>

      <div className="persona-thinking-orbits" aria-hidden="true">
        <div className="orbit orbit-1" />
        <div className="orbit orbit-2" />
      </div>

      <div className="persona-speaking-sketch" aria-hidden="true">
        <div className="sketch sketch-1" />
        <div className="sketch sketch-2" />
      </div>

      <div className="persona-asleep-mark" aria-hidden="true">⌣</div>
    </div>
  );
}
