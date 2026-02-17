// AppleSpeechTranscriber.swift — Apple Speech framework STT implementation
import Speech

class AppleSpeechTranscriber: SpeechTranscriber {
    private let recognizer: SFSpeechRecognizer?
    
    init(locale: Locale = Locale(identifier: "en-US")) {
        self.recognizer = SFSpeechRecognizer(locale: locale)
    }
    
    func requestPermission() async -> Bool {
        await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
    }
    
    func transcribe(audioFileURL: URL) async throws -> String {
        guard let recognizer, recognizer.isAvailable else {
            throw TranscriberError.unavailable
        }
        
        let request = SFSpeechURLRecognitionRequest(url: audioFileURL)
        request.shouldReportPartialResults = false
        request.addsPunctuation = true
        
        return try await withCheckedThrowingContinuation { cont in
            var hasResumed = false
            
            recognizer.recognitionTask(with: request) { result, error in
                guard !hasResumed else { return }
                
                if let error {
                    hasResumed = true
                    cont.resume(throwing: error)
                    return
                }
                
                if let result, result.isFinal {
                    hasResumed = true
                    let text = result.bestTranscription.formattedString
                    if text.isEmpty {
                        cont.resume(throwing: TranscriberError.noResult)
                    } else {
                        cont.resume(returning: text)
                    }
                }
            }
        }
    }
}
