export class ElevenLabsTts {
    apiKey;
    voiceId;
    modelId;
    outputFormat;
    stability;
    similarityBoost;
    style;
    useSpeakerBoost;
    constructor() {
        this.apiKey = process.env.ELEVENLABS_API_KEY ?? "";
        this.voiceId = process.env.ELEVENLABS_VOICE_ID ?? "";
        this.modelId = process.env.ELEVENLABS_MODEL_ID ?? "eleven_multilingual_v2";
        this.outputFormat = process.env.ELEVENLABS_OUTPUT_FORMAT ?? "mp3_44100_128";
        this.stability = Number(process.env.ELEVENLABS_STABILITY ?? "0.45");
        this.similarityBoost = Number(process.env.ELEVENLABS_SIMILARITY_BOOST ?? "0.78");
        this.style = Number(process.env.ELEVENLABS_STYLE ?? "0.2");
        this.useSpeakerBoost = (process.env.ELEVENLABS_USE_SPEAKER_BOOST ?? "true").toLowerCase() !== "false";
        if (!this.apiKey)
            throw new Error("ELEVENLABS_API_KEY is required for TTS");
        if (!this.voiceId)
            throw new Error("ELEVENLABS_VOICE_ID is required for TTS");
    }
    getDebugConfig() {
        return {
            voiceId: this.voiceId,
            modelId: this.modelId,
            outputFormat: this.outputFormat,
            stability: this.stability,
            similarityBoost: this.similarityBoost,
            style: this.style,
            useSpeakerBoost: this.useSpeakerBoost
        };
    }
    async streamSpeak(text, onChunk, signal) {
        const url = `https://api.elevenlabs.io/v1/text-to-speech/${this.voiceId}/stream?output_format=${encodeURIComponent(this.outputFormat)}`;
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
                    stability: this.stability,
                    similarity_boost: this.similarityBoost,
                    style: this.style,
                    use_speaker_boost: this.useSpeakerBoost
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
            if (done)
                break;
            if (!value || value.length === 0)
                continue;
            onChunk(Buffer.from(value).toString("base64"));
        }
    }
}
