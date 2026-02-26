export type VoiceState =
  | "idle"
  | "requesting_mic"
  | "gated"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

export type VoiceEvent =
  | "START"
  | "MIC_GRANTED"
  | "MIC_DENIED"
  | "EMAIL_VERIFIED"
  | "CONNECTED"
  | "ASSISTANT_THINKING"
  | "ASSISTANT_SPEAKING"
  | "END_SPEAKING"
  | "END"
  | "FAIL";

const transitions: Record<VoiceState, Partial<Record<VoiceEvent, VoiceState>>> = {
  idle: { START: "requesting_mic" },
  requesting_mic: { MIC_GRANTED: "gated", MIC_DENIED: "error", FAIL: "error" },
  gated: { EMAIL_VERIFIED: "connecting", END: "idle" },
  connecting: { CONNECTED: "listening", FAIL: "error", END: "idle" },
  listening: { ASSISTANT_THINKING: "thinking", END: "idle", FAIL: "error" },
  thinking: { ASSISTANT_SPEAKING: "speaking", END: "idle", FAIL: "error" },
  speaking: { END_SPEAKING: "listening", END: "idle", FAIL: "error" },
  error: { END: "idle", START: "requesting_mic" }
};

export function nextState(state: VoiceState, event: VoiceEvent): VoiceState {
  return transitions[state][event] ?? state;
}
