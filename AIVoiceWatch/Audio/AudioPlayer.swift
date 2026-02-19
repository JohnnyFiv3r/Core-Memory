// AudioPlayer.swift — Robust audio playback for watchOS
// Uses extended audio session and AVAudioPlayerNode for reliable full playback.
import AVFoundation
import WatchKit

class AudioPlayer: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var session: AVAudioSession { AVAudioSession.sharedInstance() }
    var onPlaybackComplete: (() -> Void)?

    func play(url: URL) {
        do {
            // Request extended runtime session to prevent watchOS from suspending
            try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            player = try AVAudioPlayer(contentsOf: url)
            player?.delegate = self
            player?.volume = 1.0

            // Log duration for debugging
            let duration = player?.duration ?? 0
            print("AudioPlayer: playing \(url.lastPathComponent), duration: \(String(format: "%.1f", duration))s")

            player?.prepareToPlay()
            let success = player?.play() ?? false
            print("AudioPlayer: play() returned \(success)")

            if !success {
                print("AudioPlayer: play() returned false")
                onPlaybackComplete?()
            }
        } catch {
            print("AudioPlayer: Failed to play: \(error)")
            onPlaybackComplete?()
        }
    }

    func stop() {
        player?.stop()
        player = nil
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
    }

    var isPlaying: Bool {
        player?.isPlaying ?? false
    }

    // MARK: - AVAudioPlayerDelegate

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        print("AudioPlayer: finished, success=\(flag)")
        self.player = nil
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        print("AudioPlayer: decode error: \(error?.localizedDescription ?? "unknown")")
        self.player = nil
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
        onPlaybackComplete?()
    }
}
