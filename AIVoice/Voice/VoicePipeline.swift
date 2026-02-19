// VoicePipeline.swift — Coordinates STT and TTS processing
import Foundation

@Observable
class VoicePipeline {
    private let transcriber: SpeechTranscriber
    private var piperSynthesizer: PiperSpeechSynthesizer?
    private let appleSynthesizer: SpeechSynthesizer

    var isProcessing = false
    var lastTranscription: String?
    var lastError: Error?

    init(transcriber: SpeechTranscriber? = nil,
         synthesizer: SpeechSynthesizer = AppleSpeechSynthesizer()) {
        // Use AssemblyAI if key is configured, otherwise fall back to Apple Speech
        if let transcriber = transcriber {
            self.transcriber = transcriber
        } else {
            let assemblyKey = try? SecureStorage.load(key: "assemblyai_api_key")
            if let key = assemblyKey, !key.isEmpty {
                self.transcriber = AssemblyAITranscriber()
            } else {
                self.transcriber = AppleSpeechTranscriber()
            }
        }
        self.appleSynthesizer = synthesizer

        // Set up Piper if gateway is configured
        loadPiperConfig()
    }

    /// Reload Piper config (call after saving new gateway credentials)
    func loadPiperConfig() {
        let gatewayURL = (try? SecureStorage.load(key: "openclaw_gateway_url")) ?? ""
        let gatewayToken = (try? SecureStorage.load(key: "openclaw_gateway_token")) ?? ""

        if !gatewayURL.isEmpty && !gatewayToken.isEmpty {
            piperSynthesizer = PiperSpeechSynthesizer(gatewayURL: gatewayURL, token: gatewayToken)
            print("[VoicePipeline] Piper TTS configured via \(gatewayURL)")
        } else {
            piperSynthesizer = nil
            print("[VoicePipeline] Using Apple TTS (no gateway configured)")
        }
    }

    /// Transcribe audio file from watch → returns text for agent
    func processIncoming(audioURL: URL) async throws -> String {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }

        let fileSize = (try? FileManager.default.attributesOfItem(atPath: audioURL.path)[.size] as? Int) ?? 0
        let engine = transcriber is AssemblyAITranscriber ? "AssemblyAI" : "Apple Speech"
        print("[VoicePipeline] Processing \(fileSize) bytes with \(engine): \(audioURL.lastPathComponent)")

        do {
            let text = try await transcriber.transcribe(audioFileURL: audioURL)
            print("[VoicePipeline] Transcribed: \"\(text)\"")
            lastTranscription = text
            return text
        } catch {
            print("[VoicePipeline] Error: \(error.localizedDescription)")
            lastError = error
            throw error
        }
    }

    /// Synthesize agent response → returns audio file URL for watch
    /// Tries Piper (server-side) first, falls back to Apple TTS
    func processResponse(text: String) async throws -> URL {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }

        // Try Piper first
        if let piper = piperSynthesizer {
            print("[VoicePipeline] Attempting Piper TTS...")
            do {
                let url = try await piper.synthesize(text: text)
                let fileSize = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
                print("[VoicePipeline] TTS via Piper: \(url.lastPathComponent) (\(fileSize) bytes)")
                return url
            } catch {
                print("[VoicePipeline] ⚠️ Piper failed: \(error.localizedDescription)")
                print("[VoicePipeline] Falling back to Apple TTS")
            }
        } else {
            print("[VoicePipeline] Piper not configured, using Apple TTS")
        }

        // Fallback to Apple TTS
        do {
            let url = try await appleSynthesizer.synthesize(text: text)
            print("[VoicePipeline] TTS via Apple: \(url.lastPathComponent)")
            return url
        } catch {
            lastError = error
            throw error
        }
    }
}
