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

        // Use CAF (Core Audio Format) — native to Apple, no encoding overhead
        let outputURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".caf")

        return try await withCheckedThrowingContinuation { cont in
            var hasResumed = false
            var audioFile: AVAudioFile?
            var totalFrames: AVAudioFrameCount = 0
            var lastBufferHash: Int = 0

            synthesizer.write(utterance) { buffer in
                guard !hasResumed else { return }

                guard let pcmBuffer = buffer as? AVAudioPCMBuffer,
                      pcmBuffer.frameLength > 0 else {
                    // Empty buffer = done
                    hasResumed = true
                    audioFile = nil
                    if totalFrames > 0 {
                        print("AppleTTS: wrote \(totalFrames) frames to \(outputURL.lastPathComponent)")
                        cont.resume(returning: outputURL)
                    } else {
                        cont.resume(throwing: SynthesizerError.noAudio)
                    }
                    return
                }

                // Deduplicate: AVSpeechSynthesizer.write() sometimes delivers
                // the same initial buffer multiple times (causes stutter)
                let bufferHash = self.hashBuffer(pcmBuffer)
                if bufferHash == lastBufferHash && totalFrames < 4800 {
                    // Skip duplicate buffer (only check early in stream)
                    print("AppleTTS: skipping duplicate buffer at frame \(totalFrames)")
                    return
                }
                lastBufferHash = bufferHash

                do {
                    if audioFile == nil {
                        // Write as Linear PCM in CAF container
                        let settings: [String: Any] = [
                            AVFormatIDKey: Int(kAudioFormatLinearPCM),
                            AVSampleRateKey: pcmBuffer.format.sampleRate,
                            AVNumberOfChannelsKey: 1,
                            AVLinearPCMBitDepthKey: 16,
                            AVLinearPCMIsFloatKey: false,
                            AVLinearPCMIsBigEndianKey: false
                        ]
                        audioFile = try AVAudioFile(forWriting: outputURL, settings: settings)
                        print("AppleTTS: format \(pcmBuffer.format.sampleRate)Hz, channels \(pcmBuffer.format.channelCount)")
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

    /// Quick hash of first N samples to detect duplicate buffers
    private func hashBuffer(_ buffer: AVAudioPCMBuffer) -> Int {
        guard let channelData = buffer.floatChannelData else { return 0 }
        let ptr = channelData[0]
        let samplesToHash = min(Int(buffer.frameLength), 64)
        var hash = 0
        for i in 0..<samplesToHash {
            hash = hash &+ Int(ptr[i] * 10000)
        }
        return hash
    }
}
