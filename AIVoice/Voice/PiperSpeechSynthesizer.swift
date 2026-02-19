// PiperSpeechSynthesizer.swift — Server-side Piper TTS via HTTP
import Foundation

class PiperSpeechSynthesizer: SpeechSynthesizer {
    private let ttsURL: String
    private let token: String
    private let session: URLSession

    init(gatewayURL: String, token: String) {
        self.token = token

        // Derive TTS URL from gateway URL
        var derivedURL = gatewayURL  // fallback
        if let url = URL(string: gatewayURL), let host = url.host {
            if host.hasPrefix("api.") {
                let ttsHost = "tts." + String(host.dropFirst(4))
                var components = URLComponents()
                components.scheme = url.scheme ?? "https"
                components.host = ttsHost
                components.path = "/tts"
                if let built = components.url?.absoluteString {
                    derivedURL = built
                }
            } else {
                var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
                components.port = 18790
                components.path = "/tts"
                if let built = components.url?.absoluteString {
                    derivedURL = built
                }
            }
        }
        self.ttsURL = derivedURL
        print("PiperTTS: initialized with URL: \(ttsURL)")

        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func synthesize(text: String) async throws -> URL {
        guard let url = URL(string: ttsURL) else {
            print("PiperTTS: ❌ invalid URL: \(ttsURL)")
            throw PiperError.invalidURL
        }

        print("PiperTTS: POST \(url.absoluteString) with \(text.count) chars")

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let body = try JSONSerialization.data(withJSONObject: ["text": text])
        request.httpBody = body

        let (data, httpResponse) = try await session.data(for: request)

        guard let http = httpResponse as? HTTPURLResponse else {
            print("PiperTTS: ❌ not an HTTP response")
            throw PiperError.invalidResponse
        }

        print("PiperTTS: response HTTP \(http.statusCode), \(data.count) bytes")

        guard http.statusCode == 200 else {
            let responseBody = String(data: data, encoding: .utf8) ?? "unknown"
            print("PiperTTS: ❌ HTTP \(http.statusCode): \(responseBody)")
            throw PiperError.httpError(status: http.statusCode, body: responseBody)
        }

        guard data.count > 44 else {
            print("PiperTTS: ❌ response too small (\(data.count) bytes)")
            throw PiperError.emptyAudio
        }

        // Write to temp file
        let outputURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".wav")
        try data.write(to: outputURL)

        print("PiperTTS: ✅ saved \(data.count) bytes to \(outputURL.lastPathComponent)")
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
