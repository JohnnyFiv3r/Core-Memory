// AppleSpeechSynthesizer.swift — Apple AVSpeechSynthesizer TTS implementation
import AVFoundation

class AppleSpeechSynthesizer: NSObject, SpeechSynthesizer {
    private let synthesizer = AVSpeechSynthesizer()
    private let voiceLanguage: String
    
    init(language: String = "en-US") {
        self.voiceLanguage = language
        super.init()
    }
    
    func synthesize(text: String) async throws -> URL {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: voiceLanguage)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = 1.0
        
        let outputURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".m4a")
        
        var audioBuffers: [AVAudioPCMBuffer] = []
        
        return try await withCheckedThrowingContinuation { cont in
            var hasResumed = false
            
            synthesizer.write(utterance) { buffer in
                guard !hasResumed else { return }
                
                guard let pcmBuffer = buffer as? AVAudioPCMBuffer,
                      pcmBuffer.frameLength > 0 else {
                    // Empty buffer signals completion
                    hasResumed = true
                    do {
                        let url = try self.writeBuffersToFile(audioBuffers, outputURL: outputURL)
                        cont.resume(returning: url)
                    } catch {
                        cont.resume(throwing: error)
                    }
                    return
                }
                audioBuffers.append(pcmBuffer)
            }
        }
    }
    
    private func writeBuffersToFile(_ buffers: [AVAudioPCMBuffer], outputURL: URL) throws -> URL {
        guard let firstBuffer = buffers.first else {
            throw SynthesizerError.noAudio
        }
        
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: firstBuffer.format.sampleRate,
            AVNumberOfChannelsKey: 1
        ]
        
        let audioFile = try AVAudioFile(forWriting: outputURL, settings: settings)
        for buffer in buffers {
            try audioFile.write(from: buffer)
        }
        return outputURL
    }
}
