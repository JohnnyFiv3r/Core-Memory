// AudioPlayer.swift — watchOS background-audio-compliant playback
// Implements all Apple-recommended watchOS audio workarounds:
// 1. Background audio mode (UIBackgroundModes in Info.plist)
// 2. routeSharingPolicy: .longFormAudio
// 3. Strong reference to AVAudioSession.sharedInstance()
// 4. Interruption handler with .shouldResume
import AVFoundation
import WatchKit

class AudioPlayer: NSObject {
    private var player: AVPlayer?
    private var playerItem: AVPlayerItem?
    private var endObserver: Any?
    private var failObserver: Any?
    private var timeObserver: Any?
    private var completionCalled = false
    private var pendingURL: URL?

    // Strong reference to audio session (watchOS workaround #3)
    private let audioSession = AVAudioSession.sharedInstance()

    var onPlaybackComplete: (() -> Void)?

    override init() {
        super.init()
        // Interruption handler (watchOS workaround #4)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleInterruption),
            name: AVAudioSession.interruptionNotification,
            object: audioSession
        )
    }

    deinit {
        cleanup()
        NotificationCenter.default.removeObserver(self)
    }

    /// Configure audio session for background playback (call early)
    func configureSession() {
        do {
            // watchOS workaround #2: longFormAudio route sharing
            try audioSession.setCategory(
                .playback,
                mode: .default,
                policy: .longFormAudio
            )
            try audioSession.setActive(true)
            print("AudioPlayer: session configured — category=\(audioSession.category.rawValue), active=true")
        } catch {
            print("AudioPlayer: ❌ session config error: \(error)")
        }
    }

    func play(url: URL) {
        cleanup()
        completionCalled = false
        pendingURL = url

        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = attrs?[.size] as? Int ?? 0
        print("AudioPlayer: file \(url.lastPathComponent), size: \(fileSize) bytes")
        guard fileSize > 0 else {
            print("AudioPlayer: empty file")
            fireCompletion()
            return
        }

        // Ensure session is active (may have been interrupted)
        do {
            try audioSession.setCategory(
                .playback,
                mode: .default,
                policy: .longFormAudio
            )
            try audioSession.setActive(true)
        } catch {
            print("AudioPlayer: ❌ session activation error: \(error)")
            fireCompletion()
            return
        }

        let item = AVPlayerItem(url: url)
        let avPlayer = AVPlayer(playerItem: item)
        avPlayer.volume = 1.0
        avPlayer.automaticallyWaitsToMinimizeStalling = false

        self.playerItem = item
        self.player = avPlayer

        // Observe playback end
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            print("AudioPlayer: ✅ played to end")
            self?.fireCompletion()
        }

        // Observe failures
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] notification in
            let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            print("AudioPlayer: ❌ failed: \(error?.localizedDescription ?? "unknown")")
            self?.fireCompletion()
        }

        // Progress logging
        let interval = CMTime(seconds: 2.0, preferredTimescale: 1)
        timeObserver = avPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            guard let self = self, let item = self.playerItem else { return }
            let current = CMTimeGetSeconds(time)
            let total = CMTimeGetSeconds(item.duration)
            let rate = self.player?.rate ?? 0
            print("AudioPlayer: \(String(format: "%.1f", current))/\(String(format: "%.1f", total))s rate=\(rate)")
        }

        let duration = CMTimeGetSeconds(item.asset.duration)
        print("AudioPlayer: duration \(String(format: "%.1f", duration))s, starting")

        // Safety timeout
        let safetyDelay = max(duration + 10.0, 15.0)
        DispatchQueue.main.asyncAfter(deadline: .now() + safetyDelay) { [weak self] in
            guard let self = self, !self.completionCalled else { return }
            print("AudioPlayer: safety timeout")
            self.fireCompletion()
        }

        avPlayer.play()
        print("AudioPlayer: play() called, rate=\(avPlayer.rate)")
    }

    func stop() {
        player?.pause()
        fireCompletion()
    }

    var isPlaying: Bool {
        player?.rate ?? 0 > 0
    }

    // MARK: - Interruption handling (watchOS workaround #4)

    @objc private func handleInterruption(_ notification: Notification) {
        guard let info = notification.userInfo,
              let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

        switch type {
        case .began:
            print("AudioPlayer: ⚠️ interruption began")
            // Don't clean up — wait for .ended with .shouldResume

        case .ended:
            let options = info[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            let shouldResume = AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume)
            print("AudioPlayer: interruption ended, shouldResume=\(shouldResume)")

            if shouldResume {
                // Reactivate and resume playback
                do {
                    try audioSession.setActive(true)
                    player?.play()
                    print("AudioPlayer: ✅ resumed after interruption")
                } catch {
                    print("AudioPlayer: ❌ failed to resume: \(error)")
                    fireCompletion()
                }
            } else {
                print("AudioPlayer: system says don't resume — cleaning up")
                fireCompletion()
            }

        @unknown default:
            break
        }
    }

    // MARK: - Cleanup

    private func cleanup() {
        player?.pause()
        if let obs = endObserver {
            NotificationCenter.default.removeObserver(obs)
            endObserver = nil
        }
        if let obs = failObserver {
            NotificationCenter.default.removeObserver(obs)
            failObserver = nil
        }
        if let obs = timeObserver, let p = player {
            p.removeTimeObserver(obs)
            timeObserver = nil
        }
        player = nil
        playerItem = nil
    }

    private func fireCompletion() {
        guard !completionCalled else { return }
        completionCalled = true
        let urlToDelete = pendingURL
        cleanup()
        try? audioSession.setActive(false, options: .notifyOthersOnDeactivation)
        if let url = urlToDelete {
            try? FileManager.default.removeItem(at: url)
        }
        onPlaybackComplete?()
    }
}
