import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef } from "react";
export function mapVoiceToPersonaState(state) {
    if (state === "listening")
        return "listening";
    if (state === "thinking")
        return "thinking";
    if (state === "speaking")
        return "speaking";
    return "idle";
}
export function Persona({ state, variant = "halo", className, onReady, onLoad, onLoadError, onPlay, onPause, onStop }) {
    const mountedRef = useRef(false);
    useEffect(() => {
        try {
            onLoad?.();
            onReady?.();
            mountedRef.current = true;
        }
        catch (e) {
            onLoadError?.(e);
        }
        return () => {
            onStop?.();
        };
    }, [onLoad, onReady, onLoadError, onStop]);
    useEffect(() => {
        if (!mountedRef.current)
            return;
        if (state === "idle" || state === "asleep")
            onPause?.();
        else
            onPlay?.();
    }, [state, onPlay, onPause]);
    return (_jsxs("div", { className: `orb-wrap persona-${variant} halo-state-${state} ${className ?? ""}`.trim(), "aria-label": `Persona state: ${state}`, children: [_jsx("div", { className: "halo-ring halo-ring-outer" }), _jsx("div", { className: "halo-ring halo-ring-mid" }), _jsx("div", { className: "halo-ring halo-ring-inner" }), _jsx("div", { className: "halo-core" })] }));
}
