// ChatConfiguration.swift — Stream Chat SDK setup
import Foundation
// import StreamChat
// import StreamChatSwiftUI

/// Stream Chat configuration for AI Voice
/// Note: Requires StreamChat SDK via SPM:
///   https://github.com/GetStream/stream-chat-swiftui
class ChatConfiguration {
    /// Stream Chat API key from dashboard (https://getstream.io)
    /// Store in SecureStorage for production; hardcoded here for MVP setup
    static var apiKey: String {
        (try? SecureStorage.load(key: "stream_api_key")) ?? ""
    }
    
    /// Configure and connect Stream Chat client
    /// Call this once at app launch after API key is configured
    static func setup() {
        // Uncomment when StreamChat SDK is added via SPM:
        /*
        let config = ChatClientConfig(apiKeyString: apiKey)
        config.isLocalStorageEnabled = true
        
        let client = ChatClient(config: config)
        
        // MVP: development token (no auth server needed)
        let userId = "johnny5"
        let token = Token.development(userId: userId)
        
        client.connectUser(
            userInfo: .init(id: userId, name: "Johnny5"),
            token: token
        )
        
        ChatManager.shared.setup(client: client)
        */
    }
}
