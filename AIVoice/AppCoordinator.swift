// AppCoordinator.swift — Central coordinator wiring connectivity, voice pipeline, and agent
import Foundation

@Observable
class AppCoordinator {
    let connectivity = PhoneConnectivityManager()
    let voicePipeline = VoicePipeline()
    let agentManager = AgentManager()
    let chatManager = ChatManager.shared
    
    var isProcessing = false
    var lastError: String?
    
    func start() {
        connectivity.activate()
        agentManager.loadDefaults()
        
        connectivity.onVoiceMessageReceived = { [weak self] audioURL in
            Task { await self?.handleVoiceMessage(audioURL) }
        }
    }
    
    private func handleVoiceMessage(_ audioURL: URL) async {
        isProcessing = true
        lastError = nil
        defer { isProcessing = false }
        
        do {
            // Step 1: Transcribe audio to text
            let text = try await voicePipeline.processIncoming(audioURL: audioURL)
            
            // Step 2: Send to agent via Telegram
            guard let agent = agentManager.activeAgent else {
                throw AgentError.noActiveAgent
            }
            let response = try await agent.send(message: text)
            
            // Step 3: Synthesize response to audio
            let ttsURL = try await voicePipeline.processResponse(text: response)
            
            // Step 4: Send audio back to watch
            connectivity.sendTTSAudio(fileURL: ttsURL, responseText: response)
            
            // Step 5: Store in chat history
            chatManager.recordSentMessage(text: text)
            chatManager.recordReceivedMessage(text: response)
            
        } catch {
            lastError = error.localizedDescription
            if let errorAudio = try? await voicePipeline.processResponse(
                text: "Sorry, I couldn't process that. \(error.localizedDescription)"
            ) {
                connectivity.sendTTSAudio(fileURL: errorAudio, responseText: "Error: \(error.localizedDescription)")
            }
        }
    }
}

enum AgentError: Error, LocalizedError {
    case noActiveAgent
    
    var errorDescription: String? {
        switch self {
        case .noActiveAgent: return "No active agent configured"
        }
    }
}
