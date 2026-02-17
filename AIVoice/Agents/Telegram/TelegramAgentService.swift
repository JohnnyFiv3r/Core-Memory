// TelegramAgentService.swift — AgentService implementation via Telegram Bot API
import Foundation

class TelegramAgentService: AgentService {
    let agentName: String
    private let api: TelegramAPI
    private let chatId: String
    private var pollTask: Task<Void, Never>?
    private var lastUpdateId: Int?
    
    init(config: AgentConfiguration) {
        self.agentName = config.name
        self.api = TelegramAPI(botToken: config.config["botToken"] ?? "")
        self.chatId = config.config["chatId"] ?? ""
    }
    
    func send(message: String) async throws -> String {
        // Send message to agent
        let sent = try await api.sendMessage(chatId: chatId, text: message)
        
        // Wait for response
        let response = try await waitForResponse(afterMessageId: sent.message_id)
        return response
    }
    
    private func waitForResponse(afterMessageId: Int, timeout: TimeInterval = 90) async throws -> String {
        let deadline = Date().addingTimeInterval(timeout)
        
        while Date() < deadline {
            // Check for cancellation
            try Task.checkCancellation()
            
            let updates = try await api.getUpdates(
                offset: lastUpdateId.map { $0 + 1 },
                timeout: 5
            )
            
            for update in updates {
                lastUpdateId = update.update_id
                
                // Look for a response message (not from us, i.e., from the bot/agent)
                if let msg = update.message,
                   let text = msg.text,
                   msg.message_id > afterMessageId {
                    return text
                }
            }
        }
        
        throw TelegramError.timeout
    }
    
    func startListening(onMessage: @escaping (String) -> Void) {
        pollTask = Task {
            while !Task.isCancelled {
                do {
                    let updates = try await api.getUpdates(
                        offset: lastUpdateId.map { $0 + 1 },
                        timeout: 30
                    )
                    
                    for update in updates {
                        lastUpdateId = update.update_id
                        if let text = update.message?.text {
                            onMessage(text)
                        }
                    }
                } catch {
                    // Back off on error
                    if !Task.isCancelled {
                        try? await Task.sleep(for: .seconds(5))
                    }
                }
            }
        }
    }
    
    func stopListening() {
        pollTask?.cancel()
        pollTask = nil
    }
}
