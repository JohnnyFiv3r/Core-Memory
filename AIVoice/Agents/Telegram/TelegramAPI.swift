// TelegramAPI.swift — Telegram Bot API client
import Foundation

class TelegramAPI {
    private let botToken: String
    private let baseURL: URL
    private let session: URLSession
    
    init(botToken: String) {
        self.botToken = botToken
        self.baseURL = URL(string: "https://api.telegram.org/bot\(botToken)")!
        
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 35  // slightly longer than long-poll timeout
        self.session = URLSession(configuration: config)
    }
    
    func sendMessage(chatId: String, text: String) async throws -> TelegramMessage {
        let url = baseURL.appendingPathComponent("sendMessage")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let body = SendMessageBody(chat_id: chatId, text: text)
        request.httpBody = try JSONEncoder().encode(body)
        
        let (data, _) = try await session.data(for: request)
        let response = try JSONDecoder().decode(TelegramResponse<TelegramMessage>.self, from: data)
        
        guard response.ok, let result = response.result else {
            throw TelegramError.apiError(response.description ?? "Unknown error")
        }
        return result
    }
    
    func getUpdates(offset: Int?, timeout: Int = 30) async throws -> [TelegramUpdate] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("getUpdates"),
            resolvingAgainstBaseURL: false
        )!
        
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "timeout", value: "\(timeout)")
        ]
        if let offset {
            queryItems.append(URLQueryItem(name: "offset", value: "\(offset)"))
        }
        components.queryItems = queryItems
        
        let (data, _) = try await session.data(from: components.url!)
        let response = try JSONDecoder().decode(TelegramResponse<[TelegramUpdate]>.self, from: data)
        return response.result ?? []
    }
}
