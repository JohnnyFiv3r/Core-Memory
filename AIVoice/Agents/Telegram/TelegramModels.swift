// TelegramModels.swift — Telegram Bot API response types
import Foundation

struct TelegramResponse<T: Decodable>: Decodable {
    let ok: Bool
    let result: T?
    let description: String?
}

struct TelegramMessage: Decodable {
    let message_id: Int
    let text: String?
    let date: Int
    
    let from: TelegramUser?
}

struct TelegramUser: Decodable {
    let id: Int
    let is_bot: Bool
    let first_name: String?
}

struct TelegramUpdate: Decodable {
    let update_id: Int
    let message: TelegramMessage?
}

struct SendMessageBody: Encodable {
    let chat_id: String
    let text: String
}

enum TelegramError: Error, LocalizedError {
    case apiError(String)
    case noResponse
    case invalidToken
    case timeout
    
    var errorDescription: String? {
        switch self {
        case .apiError(let msg): return "Telegram API error: \(msg)"
        case .noResponse: return "No response from agent"
        case .invalidToken: return "Invalid Telegram bot token"
        case .timeout: return "Response timed out"
        }
    }
}
