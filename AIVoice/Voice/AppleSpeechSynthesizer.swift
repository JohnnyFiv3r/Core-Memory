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
            .appendingPathComponent(UUID().uuidString + ".wav")

        return try await withCheckedThrowingContinuation { cont in
            var hasResumed = false
            var audioFile: AVAudioFile?
            var totalFrames: AVAudioFrameCount = 0

            synthesizer.write(utterance) { buffer in
                guard !hasResumed else { return }

                guard let pcmBuffer = buffer as? AVAudioPCMBuffer,
                      pcmBuffer.frameLength > 0 else {
                    // Empty buffer = done
                    hasResumed = true
                    audioFile = nil  // close file
                    if totalFrames > 0 {
                        print("AppleTTS: wrote \(totalFrames) frames to \(outputURL.lastPathComponent)")
                        cont.resume(returning: outputURL)
                    } else {
                        cont.resume(throwing: SynthesizerError.noAudio)
                    }
                    return
                }

                do {
                    // Create file on first buffer (to get correct format)
                    if audioFile == nil {
                        let wavSettings: [String: Any] = [
                            AVFormatIDKey: Int(kAudioFormatLinearPCM),
                            AVSampleRateKey: pcmBuffer.format.sampleRate,
                            AVNumberOfChannelsKey: 1,
                            AVLinearPCMBitDepthKey: 16,
                            AVLinearPCMIsFloatKey: false,
                            AVLinearPCMIsBigEndianKey: false
                        ]
                        audioFile = try AVAudioFile(forWriting: outputURL, settings: wavSettings)
                    }
                    try audioFile?.write(from: pcmBuffer)
                    totalFrames += pcmBuffer.frameLength
                } catch {
                    guard !hasResumed else { return }
                    hasResumed = true
                    cont.resume(throwing: error)
                }
            }
        }
    }
}
