const transitions = {
    idle: { START: "requesting_mic" },
    requesting_mic: { MIC_GRANTED: "gated", MIC_DENIED: "error", FAIL: "error" },
    gated: { EMAIL_VERIFIED: "connecting", END: "idle" },
    connecting: { CONNECTED: "listening", FAIL: "error", END: "idle" },
    listening: { ASSISTANT_THINKING: "thinking", END: "idle", FAIL: "error" },
    thinking: { ASSISTANT_SPEAKING: "speaking", END: "idle", FAIL: "error" },
    speaking: { END_SPEAKING: "listening", END: "idle", FAIL: "error" },
    error: { END: "idle", START: "requesting_mic" }
};
export function nextState(state, event) {
    return transitions[state][event] ?? state;
}
