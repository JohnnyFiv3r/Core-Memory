// AudioPlayer.swift — AVPlayer-based playback for watchOS
// AVPlayer handles audio routing and session management internally,
// which may be more reliable than AVAudioEngine on watchOS.
import AVFoundation

class AudioPlayer: NSObject {
    private var player: AVPlayer?
    private var playerItem: AVPlayerItem?
    private var endObserver: Any?
    private var stallObserver: NSKeyValueObservation?
    private var completionCalled = false
    var onPlaybackComplete: (() -> Void)?

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
        } catch {
            print("AudioPlayer: session error: \(error)")
            fireCompletion()
            return
        }

        let item = AVPlayerItem(url: url)
        let avPlayer = AVPlayer(playerItem: item)
        avPlayer.volume = 1.0

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
        NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] notification in
            let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            print("AudioPlayer: ❌ failed to play to end: \(error?.localizedDescription ?? "unknown")")
            self?.fireCompletion()
        }

        // Observe stalls
        stallObserver = item.observe(\.isPlaybackLikelyToKeepUp, options: [.new]) { item, change in
            print("AudioPlayer: likelyToKeepUp=\(item.isPlaybackLikelyToKeepUp), buffered=\(item.isPlaybackBufferFull)")
        }

        // Log duration
        let duration = CMTimeGetSeconds(item.asset.duration)
        print("AudioPlayer: duration: \(String(format: "%.1f", duration))s, starting playback")

        // Safety timeout
        let safetyDelay = max(duration + 5.0, 10.0)
        DispatchQueue.main.asyncAfter(deadline: .now() + safetyDelay) { [weak self] in
            guard let self = self, !self.completionCalled else { return }
            print("AudioPlayer: safety timeout after \(safetyDelay)s")
            self.fireCompletion()
        }

        avPlayer.play()
    }

    func stop() {
        player?.pause()
        fireCompletion()
    }

    var isPlaying: Bool {
        player?.rate ?? 0 > 0
    }

    private func fireCompletion() {
        guard !completionCalled else { return }
        completionCalled = true

        player?.pause()
        if let obs = endObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        stallObserver?.invalidate()
        player = nil
        playerItem = nil
        endObserver = nil
        stallObserver = nil

        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }

    deinit {
        if let obs = endObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        stallObserver?.invalidate()
    }
}
