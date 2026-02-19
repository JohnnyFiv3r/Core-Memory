// AudioPlayer.swift — AVAudioEngine-based playback for watchOS
import AVFoundation

class AudioPlayer: NSObject {
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    var onPlaybackComplete: (() -> Void)?

    func play(url: URL) {
        // Verify file
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = attrs?[.size] as? Int ?? 0
        print("AudioPlayer: file \(url.lastPathComponent), size: \(fileSize) bytes")
        guard fileSize > 0 else {
            print("AudioPlayer: empty file")
            onPlaybackComplete?()
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default)
            try session.setActive(true)

            let audioFile = try AVAudioFile(forReading: url)
            let format = audioFile.processingFormat
            let frameCount = AVAudioFrameCount(audioFile.length)
            let duration = Double(audioFile.length) / format.sampleRate
            print("AudioPlayer: duration: \(String(format: "%.1f", duration))s, frames: \(frameCount), rate: \(format.sampleRate)")

            let engine = AVAudioEngine()
            let player = AVAudioPlayerNode()

            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)

            // Read entire file into a buffer
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)!
            try audioFile.read(into: buffer)

            try engine.start()

            self.engine = engine
            self.playerNode = player

            // Schedule buffer and get notified on completion
            player.scheduleBuffer(buffer, at: nil, options: []) { [weak self] in
                DispatchQueue.main.async {
                    print("AudioPlayer: buffer completed")
                    self?.cleanup()
                }
            }

            player.play()
            print("AudioPlayer: engine playing")

        } catch {
            print("AudioPlayer: error: \(error)")
            cleanup()
        }
    }

    func stop() {
        playerNode?.stop()
        cleanup()
    }

    var isPlaying: Bool {
        playerNode?.isPlaying ?? false
    }

    private func cleanup() {
        playerNode?.stop()
        engine?.stop()
        playerNode = nil
        engine = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }
}
