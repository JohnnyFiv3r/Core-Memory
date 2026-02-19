// PiperSpeechSynthesizer.swift — Server-side Piper TTS via HTTP
import Foundation

class PiperSpeechSynthesizer: SpeechSynthesizer {
    private let gatewayURL: String
    private let token: String
    private let session: URLSession

    init(gatewayURL: String, token: String) {
        // TTS server runs on same host, port 18790
        // Derive TTS URL from gateway URL by swapping the port
        if let url = URL(string: gatewayURL),
           let host = url.host,
           let scheme = url.scheme {
            // For tunnel URLs, TTS is at /tts on a different port
            // For now, use same base URL with /tts path
            // The tunnel needs to route 18790 too, or we use the gateway to proxy
            self.gatewayURL = gatewayURL
        } else {
            self.gatewayURL = gatewayURL
        }
        self.token = token

        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func synthesize(text: String) async throws -> URL {
        // Build TTS URL — use /tts path on the gateway base
        guard var components = URLComponents(string: gatewayURL) else {
            throw PiperError.invalidURL
        }
        components.path = "/tts"

        guard let url = components.url else {
            throw PiperError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let body = try JSONSerialization.data(withJSONObject: ["text": text])
        request.httpBody = body

        let (data, httpResponse) = try await session.data(for: request)

        guard let http = httpResponse as? HTTPURLResponse else {
            throw PiperError.invalidResponse
        }

        guard http.statusCode == 200 else {
            let body = String(data: data, encoding: .utf8) ?? "unknown"
            throw PiperError.httpError(status: http.statusCode, body: body)
        }

        // Verify we got audio
        guard data.count > 44 else {  // WAV header is 44 bytes minimum
            throw PiperError.emptyAudio
        }

        // Write to temp file
        let outputURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".wav")
        try data.write(to: outputURL)

        print("PiperTTS: received \(data.count) bytes, saved to \(outputURL.lastPathComponent)")
        return outputURL
    }
}

enum PiperError: Error, LocalizedError {
    case invalidURL
    case invalidResponse
    case httpError(status: Int, body: String)
    case emptyAudio

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid Piper TTS URL"
        case .invalidResponse:
            return "Invalid response from Piper TTS"
        case .httpError(let status, let body):
            return "Piper TTS HTTP \(status): \(body.prefix(200))"
        case .emptyAudio:
            return "Piper TTS returned empty audio"
        }
    }
}
