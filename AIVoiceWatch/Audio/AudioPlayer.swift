// AudioPlayer.swift — AVAudioPlayer wrapper with interrupt support for WatchOS
import AVFoundation

class AudioPlayer: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    var onPlaybackComplete: (() -> Void)?
    
    func play(url: URL) {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            
            player = try AVAudioPlayer(contentsOf: url)
            player?.delegate = self
            player?.prepareToPlay()
            player?.play()
        } catch {
            print("AudioPlayer: Failed to play: \(error)")
            onPlaybackComplete?()
        }
    }
    
    func stop() {
        player?.stop()
        player = nil
        
        let session = AVAudioSession.sharedInstance()
        try? session.setActive(false)
    }
    
    var isPlaying: Bool {
        player?.isPlaying ?? false
    }
    
    // MARK: - AVAudioPlayerDelegate
    
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        let session = AVAudioSession.sharedInstance()
        try? session.setActive(false)
        onPlaybackComplete?()
    }
}
