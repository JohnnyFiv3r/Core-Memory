// AudioPlayer.swift — AVAudioEngine-based playback for watchOS
import AVFoundation

class AudioPlayer: NSObject {
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var audioBuffer: AVAudioPCMBuffer?
    private var completionCalled = false
    var onPlaybackComplete: (() -> Void)?

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
        completionCalled = false

        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = attrs?[.size] as? Int ?? 0
        print("AudioPlayer: file \(url.lastPathComponent), size: \(fileSize) bytes")
        guard fileSize > 0 else {
            print("AudioPlayer: empty file")
            fireCompletion()
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
            print("AudioPlayer: duration: \(String(format: "%.1f", duration))s")

            let engine = AVAudioEngine()
            let player = AVAudioPlayerNode()

            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)

            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)!
            try audioFile.read(into: buffer)

            try engine.start()

            self.engine = engine
            self.playerNode = player
            self.audioBuffer = buffer

            player.scheduleBuffer(buffer, at: nil, options: []) { [weak self] in
                // This fires on audio render thread — dispatch to main
                DispatchQueue.main.async {
                    print("AudioPlayer: buffer done")
                    self?.fireCompletion()
                }
            }

            player.play()
            print("AudioPlayer: playing, duration \(String(format: "%.1f", duration))s")

            // Safety fallback: force completion after duration + 3s
            let safetyDelay = duration + 3.0
            DispatchQueue.main.asyncAfter(deadline: .now() + safetyDelay) { [weak self] in
                guard let self = self, !self.completionCalled else { return }
                print("AudioPlayer: safety timeout after \(safetyDelay)s")
                self.fireCompletion()
            }

        } catch {
            print("AudioPlayer: error: \(error)")
            fireCompletion()
        }
    }

    func stop() {
        playerNode?.stop()
        engine?.stop()
        fireCompletion()
    }

    var isPlaying: Bool {
        playerNode?.isPlaying ?? false
    }

    @objc private func handleInterruption(_ notification: Notification) {
        guard let info = notification.userInfo,
              let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

        print("AudioPlayer: interruption \(type.rawValue)")
        if type == .began {
            DispatchQueue.main.async { [weak self] in
                self?.fireCompletion()
            }
        }
    }

    private func fireCompletion() {
        guard !completionCalled else { return }
        completionCalled = true

        playerNode?.stop()
        engine?.stop()
        playerNode = nil
        engine = nil
        audioBuffer = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }
}
