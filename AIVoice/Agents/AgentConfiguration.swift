// AgentConfiguration.swift — Agent config and manager
import Foundation

enum AgentType: String, Codable {
    case openclaw
    case telegram  // legacy fallback
}

struct AgentConfiguration: Codable {
    let name: String
    let type: AgentType
    let config: [String: String]
}

@Observable
class AgentManager {
    var activeAgent: AgentService?
    var availableAgents: [AgentConfiguration] = []

    func loadDefaults() {
        let gatewayURL = (try? SecureStorage.load(key: "openclaw_gateway_url")) ?? ""
        let gatewayToken = (try? SecureStorage.load(key: "openclaw_gateway_token")) ?? ""

        if !gatewayURL.isEmpty && !gatewayToken.isEmpty {
            // Preferred: direct OpenClaw gateway connection
            let config = AgentConfiguration(
                name: "Krusty",
                type: .openclaw,
                config: [
                    "gatewayURL": gatewayURL,
                    "gatewayToken": gatewayToken,
                    "agentId": "main",
                    "userId": "clawdio-voice"
                ]
            )
            availableAgents = [config]
            activeAgent = OpenClawAgentService(config: config)
        } else {
            // Fallback: Telegram Bot API (legacy)
            let botToken = (try? SecureStorage.load(key: "telegram_bot_token")) ?? ""
            let chatId = (try? SecureStorage.load(key: "telegram_chat_id")) ?? ""

            let config = AgentConfiguration(
                name: "Krusty",
                type: .telegram,
                config: [
                    "botToken": botToken,
                    "chatId": chatId
                ]
            )
            availableAgents = [config]
            activeAgent = TelegramAgentService(config: config)
        }
    }
}
