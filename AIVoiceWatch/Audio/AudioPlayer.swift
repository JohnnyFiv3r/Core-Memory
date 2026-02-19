// AudioPlayer.swift — Robust AVPlayer-based playback for watchOS
// Applies all known watchOS audio workarounds:
// - Persistent instance scoping (caller must retain as instance var)
// - Explicit session activation before every play
// - Reinitialize on wake
// - AVPlayer for reliable media playback
import AVFoundation

class AudioPlayer: NSObject {
    private var player: AVPlayer?
    private var playerItem: AVPlayerItem?
    private var endObserver: Any?
    private var failObserver: Any?
    private var timeObserver: Any?
    private var completionCalled = false
    private var pendingURL: URL?
    var onPlaybackComplete: (() -> Void)?

    override init() {
        super.init()
        // Reinitialize audio on wake (watchOS workaround)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(appDidBecomeActive),
            name: WKApplication.didBecomeActiveNotification,
            object: nil
        )
    }

    deinit {
        cleanup()
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func appDidBecomeActive() {
        // If we were playing when the app went inactive, the player may have died.
        // The safety timeout will handle cleanup.
        if let player = player, player.rate == 0 && !completionCalled {
            print("AudioPlayer: app became active, player stalled — cleaning up")
            fireCompletion()
        }
    }

    func play(url: URL) {
        // Clean up any previous playback
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

        // Explicit audio session setup (watchOS workaround)
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            print("AudioPlayer: audio session activated, category=\(session.category.rawValue), route=\(session.currentRoute.outputs.map { $0.portType.rawValue })")
        } catch {
            print("AudioPlayer: ❌ session error: \(error)")
            fireCompletion()
            return
        }

        let item = AVPlayerItem(url: url)
        let avPlayer = AVPlayer(playerItem: item)
        avPlayer.volume = 1.0
        // Disable automatic waiting — start playing immediately
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

        // Periodic time observer — logs progress and detects stalls
        let interval = CMTime(seconds: 1.0, preferredTimescale: 1)
        timeObserver = avPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            guard let self = self, let item = self.playerItem else { return }
            let current = CMTimeGetSeconds(time)
            let total = CMTimeGetSeconds(item.duration)
            let rate = self.player?.rate ?? 0
            print("AudioPlayer: \(String(format: "%.1f", current))/\(String(format: "%.1f", total))s rate=\(rate)")

            // Detect stall: time is advancing but rate dropped to 0
            if rate == 0 && current > 0 && current < total - 0.5 && !self.completionCalled {
                print("AudioPlayer: ⚠️ stall detected at \(String(format: "%.1f", current))s, attempting resume")
                self.player?.play()
            }
        }

        let duration = CMTimeGetSeconds(item.asset.duration)
        print("AudioPlayer: duration \(String(format: "%.1f", duration))s, starting")

        // Safety timeout
        let safetyDelay = max(duration + 5.0, 10.0)
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
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        // Clean up temp audio file after playback
        if let url = urlToDelete {
            try? FileManager.default.removeItem(at: url)
        }
        onPlaybackComplete?()
    }
}
