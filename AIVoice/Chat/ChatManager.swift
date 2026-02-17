// ChatManager.swift — Manages chat message storage and retrieval
import Foundation
// import StreamChat

/// Manages conversation storage via Stream Chat SDK
/// Messages are recorded as they flow through the voice pipeline
@Observable
class ChatManager {
    static let shared = ChatManager()
    
    let agentUserId = "krusty"
    let channelId = "ai-voice-krusty"
    
    // In-memory message store for MVP (before Stream SDK is wired)
    var messages: [ChatMessage] = []
    
    // private var client: ChatClient?
    // private var channelController: ChatChannelController?
    
    func setup() {
        // Uncomment when StreamChat SDK is added:
        /*
        func setup(client: ChatClient) {
            self.client = client
            let channelId = ChannelId(type: .messaging, id: self.channelId)
            channelController = client.channelController(for: channelId)
            channelController?.synchronize { error in
                if let error { print("Channel sync error: \(error)") }
            }
        }
        */
    }
    
    /// Store a sent message (user's transcribed voice)
    func recordSentMessage(text: String) {
        let message = ChatMessage(
            id: UUID().uuidString,
            text: text,
            sender: .user,
            timestamp: Date()
        )
        messages.append(message)
        
        // Stream Chat integration (uncomment when SDK added):
        /*
        channelController?.createNewMessage(text: text) { result in
            if case .failure(let error) = result {
                print("Failed to store sent message: \(error)")
            }
        }
        */
    }
    
    /// Store a received message (agent's response)
    func recordReceivedMessage(text: String) {
        let message = ChatMessage(
            id: UUID().uuidString,
            text: text,
            sender: .agent,
            timestamp: Date()
        )
        messages.append(message)
        
        // Stream Chat integration (uncomment when SDK added):
        /*
        channelController?.createNewMessage(
            text: text,
            extraData: ["sender": .string("agent")]
        ) { result in
            if case .failure(let error) = result {
                print("Failed to store received message: \(error)")
            }
        }
        */
    }
}

/// Local chat message model (used before Stream SDK is integrated)
struct ChatMessage: Identifiable {
    let id: String
    let text: String
    let sender: MessageSender
    let timestamp: Date
}

enum MessageSender {
    case user
    case agent
}
