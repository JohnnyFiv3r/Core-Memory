// AudioPlayer.swift — Robust audio playback for watchOS
import AVFoundation

class AudioPlayer: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var playbackTimer: Timer?
    var onPlaybackComplete: (() -> Void)?

    func play(url: URL) {
        // Verify file exists and has content
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let fileSize = attrs?[.size] as? Int ?? 0
        print("AudioPlayer: file \(url.lastPathComponent), size: \(fileSize) bytes")

        guard fileSize > 0 else {
            print("AudioPlayer: empty file, skipping")
            onPlaybackComplete?()
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            // Use playAndRecord to avoid category switching issues
            try session.setCategory(.playAndRecord, mode: .default)
            try session.setActive(true)

            player = try AVAudioPlayer(contentsOf: url)
            player?.delegate = self
            player?.volume = 1.0

            let duration = player?.duration ?? 0
            print("AudioPlayer: duration: \(String(format: "%.1f", duration))s")

            player?.prepareToPlay()
            let success = player?.play() ?? false
            print("AudioPlayer: play() = \(success)")

            if !success {
                cleanup()
                return
            }

            // Safety timer: if delegate never fires, force cleanup
            let safeDuration = duration + 2.0
            playbackTimer = Timer.scheduledTimer(withTimeInterval: safeDuration, repeats: false) { [weak self] _ in
                guard let self = self, self.player != nil else { return }
                print("AudioPlayer: safety timer fired after \(safeDuration)s")
                self.cleanup()
            }

        } catch {
            print("AudioPlayer: error: \(error)")
            cleanup()
        }
    }

    func stop() {
        player?.stop()
        cleanup()
    }

    var isPlaying: Bool {
        player?.isPlaying ?? false
    }

    private func cleanup() {
        playbackTimer?.invalidate()
        playbackTimer = nil
        player = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }

    // MARK: - AVAudioPlayerDelegate

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        print("AudioPlayer: finished, success=\(flag)")
        cleanup()
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        print("AudioPlayer: decode error: \(error?.localizedDescription ?? "unknown")")
        cleanup()
    }
}
