// AudioMessage.swift — Shared types for watch↔phone communication
import Foundation

enum AudioMessageType: String, Codable {
    case voiceMessage    // watch → phone (user recording)
    case agentResponse   // phone → watch (TTS audio)
}

struct AudioMessageMetadata: Codable {
    let type: AudioMessageType
    let timestamp: Date
    let responseText: String?  // included with agentResponse for chat history
}

enum ConnectivityKeys {
    static let messageType = "type"
    static let timestamp = "timestamp"
    static let responseText = "responseText"
}
