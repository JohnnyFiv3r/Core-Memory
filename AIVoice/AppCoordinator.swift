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

    // Observable state for VoiceScreen
    var transcribedText: String?
    var agentResponse: String?
    var isTranscribing = false
    var isWaitingForAgent = false
    var userTranscriptTimestamp: Date?
    var agentResponseTimestamp: Date?

    func start() {
        connectivity.activate()
        agentManager.loadDefaults()

        connectivity.onVoiceMessageReceived = { [weak self] audioURL in
            Task { await self?.handleVoiceMessage(audioURL) }
        }
    }

    /// Called by VoiceScreen after countdown or manual send
    func sendTranscript(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        transcribedText = trimmed
        userTranscriptTimestamp = Date()

        Task { await sendToAgent(trimmed) }
    }

    private func handleVoiceMessage(_ audioURL: URL) async {
        isProcessing = true
        isTranscribing = true
        lastError = nil

        do {
            let text = try await voicePipeline.processIncoming(audioURL: audioURL)
            await MainActor.run {
                isTranscribing = false
                transcribedText = text
                userTranscriptTimestamp = Date()
            }
            // Countdown / auto-send is handled by VoiceScreen
        } catch {
            await MainActor.run {
                isTranscribing = false
                isProcessing = false
                lastError = error.localizedDescription
            }
        }
    }

    private func sendToAgent(_ text: String) async {
        await MainActor.run {
            isWaitingForAgent = true
            agentResponse = nil
        }

        do {
            guard let agent = agentManager.activeAgent else {
                throw AgentError.noActiveAgent
            }
            let response = try await agent.send(message: text)

            let ttsURL = try await voicePipeline.processResponse(text: response)
            connectivity.sendTTSAudio(fileURL: ttsURL, responseText: response)

            chatManager.recordSentMessage(text: text)
            chatManager.recordReceivedMessage(text: response)

            await MainActor.run {
                agentResponse = response
                agentResponseTimestamp = Date()
                isWaitingForAgent = false
                isProcessing = false
                transcribedText = nil
            }
        } catch {
            await MainActor.run {
                lastError = error.localizedDescription
                isWaitingForAgent = false
                isProcessing = false
            }
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
