// SpeechSynthesizer.swift — Protocol for text-to-speech engines
import Foundation

protocol SpeechSynthesizer {
    func synthesize(text: String) async throws -> URL  // returns audio file URL
}

enum SynthesizerError: Error, LocalizedError {
    case noAudio
    case writeFailed
    
    var errorDescription: String? {
        switch self {
        case .noAudio: return "No audio generated"
        case .writeFailed: return "Failed to write audio file"
        }
    }
}
