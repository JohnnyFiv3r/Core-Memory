// AssemblyAITranscriber.swift — AssemblyAI REST API speech-to-text
import Foundation

class AssemblyAITranscriber: SpeechTranscriber {
    private let baseURL = "https://api.assemblyai.com/v2"
    
    private var apiKey: String? {
        try? SecureStorage.load(key: "assemblyai_api_key")
    }
    
    func transcribe(audioFileURL: URL) async throws -> String {
        guard let apiKey = apiKey, !apiKey.isEmpty else {
            throw TranscriberError.unavailable
        }
        
        // Validate audio file exists and has content
        let fileSize = (try? FileManager.default.attributesOfItem(atPath: audioFileURL.path)[.size] as? Int) ?? 0
        guard fileSize > 0 else {
            throw DetailedTranscriberError.emptyAudioFile(bytes: fileSize)
        }
        
        // Step 1: Upload audio file
        let uploadURL = try await uploadAudio(fileURL: audioFileURL, apiKey: apiKey)
        
        // Step 2: Create transcript
        let transcriptId = try await createTranscript(audioURL: uploadURL, apiKey: apiKey)
        
        // Step 3: Poll until complete
        let text = try await pollTranscript(id: transcriptId, apiKey: apiKey)
        
        guard !text.isEmpty else {
            throw DetailedTranscriberError.emptyTranscript(
                fileBytes: fileSize,
                transcriptId: transcriptId
            )
        }
        
        return text
    }
    
    private func uploadAudio(fileURL: URL, apiKey: String) async throws -> String {
        let data = try Data(contentsOf: fileURL)
        
        var request = URLRequest(url: URL(string: "\(baseURL)/upload")!)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "authorization")
        request.setValue("application/octet-stream", forHTTPHeaderField: "content-type")
        request.httpBody = data
        
        let (responseData, response) = try await URLSession.shared.data(for: request)
        
        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode != 200 {
            let body = String(data: responseData, encoding: .utf8) ?? "unknown"
            throw DetailedTranscriberError.uploadFailed(status: httpResponse.statusCode, body: body)
        }
        
        let json = try JSONSerialization.jsonObject(with: responseData) as? [String: Any]
        
        guard let uploadURL = json?["upload_url"] as? String else {
            let body = String(data: responseData, encoding: .utf8) ?? "unknown"
            throw DetailedTranscriberError.uploadFailed(status: 0, body: "No upload_url: \(body)")
        }
        
        return uploadURL
    }
    
    private func createTranscript(audioURL: String, apiKey: String) async throws -> String {
        var request = URLRequest(url: URL(string: "\(baseURL)/transcript")!)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "authorization")
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        
        let body = ["audio_url": audioURL]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (responseData, response) = try await URLSession.shared.data(for: request)
        
        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode != 200 {
            let body = String(data: responseData, encoding: .utf8) ?? "unknown"
            throw DetailedTranscriberError.transcriptCreateFailed(status: httpResponse.statusCode, body: body)
        }
        
        let json = try JSONSerialization.jsonObject(with: responseData) as? [String: Any]
        
        guard let id = json?["id"] as? String else {
            throw DetailedTranscriberError.transcriptCreateFailed(status: 0, body: "No id in response")
        }
        
        return id
    }
    
    private func pollTranscript(id: String, apiKey: String) async throws -> String {
        var request = URLRequest(url: URL(string: "\(baseURL)/transcript/\(id)")!)
        request.setValue(apiKey, forHTTPHeaderField: "authorization")
        
        for _ in 0..<60 {
            let (data, _) = try await URLSession.shared.data(for: request)
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            
            let status = json?["status"] as? String ?? ""
            
            if status == "completed" {
                // AssemblyAI returns null for text when audio has no speech
                if let text = json?["text"] as? String {
                    return text
                }
                return ""
            } else if status == "error" {
                let errorMsg = json?["error"] as? String ?? "unknown error"
                throw DetailedTranscriberError.transcriptionError(message: errorMsg)
            }
            
            try await Task.sleep(nanoseconds: 1_000_000_000)
        }
        
        throw DetailedTranscriberError.timeout
    }
}

enum DetailedTranscriberError: Error, LocalizedError {
    case emptyAudioFile(bytes: Int)
    case emptyTranscript(fileBytes: Int, transcriptId: String)
    case uploadFailed(status: Int, body: String)
    case transcriptCreateFailed(status: Int, body: String)
    case transcriptionError(message: String)
    case timeout
    
    var errorDescription: String? {
        switch self {
        case .emptyAudioFile(let bytes):
            return "Audio file empty (\(bytes) bytes)"
        case .emptyTranscript(let bytes, let id):
            return "No speech detected (\(bytes) bytes, id: \(id))"
        case .uploadFailed(let status, let body):
            return "Upload failed (HTTP \(status)): \(body)"
        case .transcriptCreateFailed(let status, let body):
            return "Transcript create failed (HTTP \(status)): \(body)"
        case .transcriptionError(let msg):
            return "AssemblyAI error: \(msg)"
        case .timeout:
            return "Transcription timed out (60s)"
        }
    }
}
