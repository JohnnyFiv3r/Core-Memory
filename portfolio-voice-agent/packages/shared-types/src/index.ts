import { z } from "zod";

export const ClientSessionStartSchema = z.object({
  type: z.literal("session.start"),
  email: z.string().email(),
  pageContext: z.string().optional(),
  ua: z.string().optional()
});

export const ClientVoiceChunkSchema = z.object({
  type: z.literal("voice.input.chunk"),
  pcm16Base64: z.string().min(1),
  seq: z.number().int().nonnegative()
});

export const ClientEventSchema = z.discriminatedUnion("type", [
  ClientSessionStartSchema,
  ClientVoiceChunkSchema,
  z.object({ type: z.literal("voice.input.end_turn") }),
  z.object({ type: z.literal("voice.interrupt") }),
  z.object({ type: z.literal("session.stop"), reason: z.string().optional() })
]);

export const ServerEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("session.ready"), sessionId: z.string(), maxMinutes: z.number().positive() }),
  z.object({ type: z.literal("stt.partial"), text: z.string() }),
  z.object({ type: z.literal("stt.final"), text: z.string() }),
  z.object({ type: z.literal("assistant.text.delta"), text: z.string() }),
  z.object({ type: z.literal("assistant.text.final"), text: z.string() }),
  z.object({
    type: z.literal("assistant.action"),
    action: z.enum(["open_project", "suggest_related", "show_metrics"]),
    payload: z.record(z.any())
  }),
  z.object({ type: z.literal("tts.audio.chunk"), audioBase64: z.string(), mime: z.string() }),
  z.object({ type: z.literal("tts.done") }),
  z.object({ type: z.literal("session.limit_warning"), secondsLeft: z.number().int().nonnegative() }),
  z.object({ type: z.literal("session.ended"), reason: z.enum(["limit", "user", "error"]) }),
  z.object({ type: z.literal("error"), code: z.string(), message: z.string() })
]);

export type ClientEvent = z.infer<typeof ClientEventSchema>;
export type ServerEvent = z.infer<typeof ServerEventSchema>;
