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
        
        // Step 1: Upload audio file
        let uploadURL = try await uploadAudio(fileURL: audioFileURL, apiKey: apiKey)
        
        // Step 2: Create transcript
        let transcriptId = try await createTranscript(audioURL: uploadURL, apiKey: apiKey)
        
        // Step 3: Poll until complete
        let text = try await pollTranscript(id: transcriptId, apiKey: apiKey)
        
        guard !text.isEmpty else {
            throw TranscriberError.noResult
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
        
        let (responseData, _) = try await URLSession.shared.data(for: request)
        let json = try JSONSerialization.jsonObject(with: responseData) as? [String: Any]
        
        guard let uploadURL = json?["upload_url"] as? String else {
            throw TranscriberError.noResult
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
        
        let (responseData, _) = try await URLSession.shared.data(for: request)
        let json = try JSONSerialization.jsonObject(with: responseData) as? [String: Any]
        
        guard let id = json?["id"] as? String else {
            throw TranscriberError.noResult
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
                return json?["text"] as? String ?? ""
            } else if status == "error" {
                throw TranscriberError.noResult
            }
            
            try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
        }
        
        throw TranscriberError.noResult
    }
}
