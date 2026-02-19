// OpenClawAgentService.swift — AgentService via OpenClaw Gateway HTTP API
import Foundation

class OpenClawAgentService: AgentService {
    let agentName: String
    private let gatewayURL: URL
    private let token: String
    private let agentId: String
    private let userId: String
    private let session: URLSession

    init(config: AgentConfiguration) {
        self.agentName = config.name
        let urlString = config.config["gatewayURL"] ?? "http://localhost:18789"
        self.gatewayURL = URL(string: urlString)!
        self.token = config.config["gatewayToken"] ?? ""
        self.agentId = config.config["agentId"] ?? "main"
        self.userId = config.config["userId"] ?? "clawdio-user"

        let sessionConfig = URLSessionConfiguration.default
        sessionConfig.timeoutIntervalForRequest = 120  // agent may take a while
        self.session = URLSession(configuration: sessionConfig)
    }

    func send(message: String) async throws -> String {
        let url = gatewayURL.appendingPathComponent("v1/chat/completions")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let body: [String: Any] = [
            "model": "openclaw:\(agentId)",
            "user": userId,
            "messages": [
                ["role": "user", "content": message]
            ]
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, httpResponse) = try await session.data(for: request)

        guard let http = httpResponse as? HTTPURLResponse else {
            throw OpenClawError.invalidResponse
        }

        guard http.statusCode == 200 else {
            let body = String(data: data, encoding: .utf8) ?? "unknown"
            throw OpenClawError.httpError(status: http.statusCode, body: body)
        }

        // Parse OpenAI-compatible response
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = json["choices"] as? [[String: Any]],
              let first = choices.first,
              let msg = first["message"] as? [String: Any],
              let content = msg["content"] as? String else {
            throw OpenClawError.parseError(
                body: String(data: data, encoding: .utf8) ?? "unparseable"
            )
        }

        return content
    }

    func startListening(onMessage: @escaping (String) -> Void) {
        // Not needed — responses come synchronously from send()
    }

    func stopListening() {
        // No-op
    }
}

enum OpenClawError: Error, LocalizedError {
    case invalidResponse
    case httpError(status: Int, body: String)
    case parseError(body: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Invalid response from OpenClaw gateway"
        case .httpError(let status, let body):
            return "OpenClaw HTTP \(status): \(body.prefix(200))"
        case .parseError(let body):
            return "Failed to parse OpenClaw response: \(body.prefix(200))"
        }
    }
}
