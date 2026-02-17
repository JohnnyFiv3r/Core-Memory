// VoicePipeline.swift — Coordinates STT and TTS processing
import Foundation

@Observable
class VoicePipeline {
    private let transcriber: SpeechTranscriber
    private let synthesizer: SpeechSynthesizer
    
    var isProcessing = false
    var lastTranscription: String?
    var lastError: Error?
    
    init(transcriber: SpeechTranscriber = AppleSpeechTranscriber(),
         synthesizer: SpeechSynthesizer = AppleSpeechSynthesizer()) {
        self.transcriber = transcriber
        self.synthesizer = synthesizer
    }
    
    /// Transcribe audio file from watch → returns text for agent
    func processIncoming(audioURL: URL) async throws -> String {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }
        
        do {
            let text = try await transcriber.transcribe(audioFileURL: audioURL)
            lastTranscription = text
            return text
        } catch {
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
