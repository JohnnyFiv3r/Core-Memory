// AgentConfiguration.swift — Agent config and manager
import Foundation

enum AgentType: String, Codable {
    case telegram
    case openai  // stubbed for future
}

struct AgentConfiguration: Codable {
    let name: String
    let type: AgentType
    let config: [String: String]  // bot token, chat ID, etc.
}

@Observable
class AgentManager {
    var activeAgent: AgentService?
    var availableAgents: [AgentConfiguration] = []
    
    func loadDefaults() {
        // MVP: Krusty via Telegram
        let botToken = (try? SecureStorage.load(key: "telegram_bot_token")) ?? ""
        let chatId = (try? SecureStorage.load(key: "telegram_chat_id")) ?? ""
        
        let krusty = AgentConfiguration(
            name: "Krusty",
            type: .telegram,
            config: [
                "botToken": botToken,
                "chatId": chatId
            ]
        )
        availableAgents = [krusty]
        activeAgent = TelegramAgentService(config: krusty)
    }
}
