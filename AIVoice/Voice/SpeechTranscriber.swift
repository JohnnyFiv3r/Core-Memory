// SpeechTranscriber.swift — Protocol for speech-to-text engines
import Foundation

protocol SpeechTranscriber {
    func transcribe(audioFileURL: URL) async throws -> String
}

enum TranscriberError: Error, LocalizedError {
    case unavailable
    case noResult
    case permissionDenied
    
    var errorDescription: String? {
        switch self {
        case .unavailable: return "Speech recognizer unavailable"
        case .noResult: return "No transcription result"
        case .permissionDenied: return "Speech recognition permission denied"
        }
    }
}
