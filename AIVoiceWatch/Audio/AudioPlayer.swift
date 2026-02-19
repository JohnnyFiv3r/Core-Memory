// AudioPlayer.swift — AVAudioEngine-based playback for watchOS
import AVFoundation

class AudioPlayer: NSObject {
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var audioBuffer: AVAudioPCMBuffer?
    private var bufferFormat: AVAudioFormat?
    private var isCleanedUp = false
    var onPlaybackComplete: (() -> Void)?

    private var interruptionTimer: Timer?

    override init() {
        super.init()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleInterruption),
            name: AVAudioSession.interruptionNotification,
            object: nil
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    func play(url: URL) {
        isCleanedUp = false

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
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)

            let audioFile = try AVAudioFile(forReading: url)
            let format = audioFile.processingFormat
            let frameCount = AVAudioFrameCount(audioFile.length)
            let duration = Double(audioFile.length) / format.sampleRate
            print("AudioPlayer: duration: \(String(format: "%.1f", duration))s, frames: \(frameCount)")

            let engine = AVAudioEngine()
            let player = AVAudioPlayerNode()

            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)

            // Read entire file into buffer
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)!
            try audioFile.read(into: buffer)

            try engine.start()

            self.engine = engine
            self.playerNode = player
            self.audioBuffer = buffer
            self.bufferFormat = format

            player.scheduleBuffer(buffer, at: nil, options: []) { [weak self] in
                DispatchQueue.main.async {
                    guard let self = self, !self.isCleanedUp else { return }
                    print("AudioPlayer: playback complete")
                    self.cleanup()
                }
            }

            player.play()
            print("AudioPlayer: playing")

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

    // MARK: - Interruption handling

    @objc private func handleInterruption(_ notification: Notification) {
        guard let info = notification.userInfo,
              let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

        print("AudioPlayer: interruption type=\(type.rawValue)")

        switch type {
        case .began:
            print("AudioPlayer: interruption began — cleaning up")
            // Don't try to resume, just clean up. watchOS interruptions rarely recover.
            DispatchQueue.main.async { [weak self] in
                self?.cleanup()
            }
        case .ended:
            print("AudioPlayer: interruption ended")
        @unknown default:
            break
        }
    }

    private func cleanup() {
        guard !isCleanedUp else { return }
        isCleanedUp = true
        playerNode?.stop()
        engine?.stop()
        playerNode = nil
        engine = nil
        audioBuffer = nil
        bufferFormat = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }
}
