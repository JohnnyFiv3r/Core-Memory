import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function readFirstExisting(paths: string[]): string {
  for (const p of paths) {
    try {
      if (fs.existsSync(p)) return fs.readFileSync(p, "utf8");
    } catch {
      // ignore and continue
    }
  }
  return "";
}

export function buildRealtimeInstructions(): string {
  const systemPrompt = readFirstExisting([
    path.resolve(process.cwd(), "apps/voice-server/context/system-prompt.md"),
    path.resolve(process.cwd(), "context/system-prompt.md"),
    path.resolve(__dirname, "../../context/system-prompt.md")
  ]);

  const profileContext = readFirstExisting([
    path.resolve(process.cwd(), "apps/voice-server/context/profile-context.md"),
    path.resolve(process.cwd(), "context/profile-context.md"),
    path.resolve(__dirname, "../../context/profile-context.md")
  ]);

  const base = [
    "You are Johnny's portfolio voice assistant.",
    "Always respond in English unless explicitly asked otherwise.",
    "Speak in first person as John Inniger.",
    "Do not claim you cannot access resume/background context; it is provided below.",
    "Never invent technologies, roles, or projects not in context.",
    "Do not volunteer long stories unprompted. Use story tool only when needed."
  ].join(" ");

  return [
    base,
    systemPrompt ? `\n\n[SECTION 1: SYSTEM PROMPT]\n${systemPrompt}` : "",
    profileContext ? `\n\n[SECTION 2: PROFILE CONTEXT]\n${profileContext}` : ""
  ]
    .filter(Boolean)
    .join("\n");
}
