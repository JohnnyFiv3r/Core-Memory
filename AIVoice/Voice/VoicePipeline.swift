// VoicePipeline.swift — Coordinates STT and TTS processing
import Foundation

@Observable
class VoicePipeline {
    private let transcriber: SpeechTranscriber
    private let synthesizer: SpeechSynthesizer
    
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
        self.synthesizer = synthesizer
    }
    
    /// Transcribe audio file from watch → returns text for agent
    func processIncoming(audioURL: URL) async throws -> String {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }
        
        // Log audio file details for debugging
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
    func processResponse(text: String) async throws -> URL {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }
        
        do {
            return try await synthesizer.synthesize(text: text)
        } catch {
            lastError = error
            throw error
        }
    }
}
