type OnChunk = (base64Audio: string) => void;

export class ElevenLabsTts {
  private apiKey: string;
  private voiceId: string;
  private modelId: string;

  constructor() {
    this.apiKey = process.env.ELEVENLABS_API_KEY ?? "";
    this.voiceId = process.env.ELEVENLABS_VOICE_ID ?? "";
    this.modelId = process.env.ELEVENLABS_MODEL_ID ?? "eleven_multilingual_v2";

    if (!this.apiKey) throw new Error("ELEVENLABS_API_KEY is required for TTS");
    if (!this.voiceId) throw new Error("ELEVENLABS_VOICE_ID is required for TTS");
  }

  async streamSpeak(text: string, onChunk: OnChunk, signal?: AbortSignal): Promise<void> {
    const url = `https://api.elevenlabs.io/v1/text-to-speech/${this.voiceId}/stream?output_format=mp3_44100_128`;

    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "xi-api-key": this.apiKey,
        "content-type": "application/json",
        accept: "audio/mpeg"
      },
      body: JSON.stringify({
        text,
        model_id: this.modelId,
        voice_settings: {
          stability: 0.45,
          similarity_boost: 0.78,
          style: 0.2,
          use_speaker_boost: true
        }
      }),
      signal
    });

    if (!resp.ok || !resp.body) {
      const body = await resp.text().catch(() => "");
      throw new Error(`ElevenLabs stream failed (${resp.status}): ${body.slice(0, 200)}`);
    }

    const reader = resp.body.getReader();

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!value || value.length === 0) continue;
      onChunk(Buffer.from(value).toString("base64"));
    }
  }
}
