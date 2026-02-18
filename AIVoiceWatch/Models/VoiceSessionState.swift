// VoiceSessionState.swift — State machine for watch voice session
import SwiftUI
import AVFoundation

enum SessionState: String {
    case idle
    case recording
    case sent
    case playing
    
    var icon: String {
        switch self {
        case .idle: return "mic.fill"
        case .recording: return "stop.fill"
        case .sent: return "checkmark"
        case .playing: return "speaker.wave.2.fill"
        }
    }
    
    var color: Color {
        switch self {
        case .idle: return .blue
        case .recording: return .red
        case .sent: return .green
        case .playing: return .blue
        }
    }
    
    var label: String {
        switch self {
        case .idle: return "WristChat"
        case .recording: return "Recording..."
        case .sent: return "Sent"
        case .playing: return "Playing..."
        }
    }
}

@Observable
class VoiceSession {
    var state: SessionState = .idle
    var audioLevel: Float = 0
    var audioFileURL: URL?
    
    private var audioRecorder: AudioRecorder?
    private var audioPlayer: AudioPlayer?
    private var meterTimer: Timer?
    let connectivity = WatchConnectivityManager()
    
    init() {
        connectivity.activate()
        connectivity.onAudioReceived = { [weak self] url in
            self?.onAudioReceived(url: url)
        }
    }
    
    func handleTap() {
        switch state {
        case .idle:
            startRecording()
        case .recording:
            stopRecordingAndSend()
        case .sent:
            break // waiting for response
        case .playing:
            interruptAndRecord()
        }
        
        // Haptic feedback on state change
        #if os(watchOS)
        WKInterfaceDevice.current().play(.click)
        #endif
    }
    
    private func startRecording() {
        let tempDir = FileManager.default.temporaryDirectory
        let fileURL = tempDir.appendingPathComponent(UUID().uuidString + ".m4a")
        audioFileURL = fileURL
        
        audioRecorder = AudioRecorder()
        audioRecorder?.startRecording(to: fileURL)
        state = .recording
        
        // Poll mic level at 15Hz
        meterTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 15.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.audioLevel = self.audioRecorder?.currentLevel() ?? 0
        }
    }
    
    private func stopRecordingAndSend() {
        meterTimer?.invalidate()
        meterTimer = nil
        audioLevel = 0
        
        audioRecorder?.stopRecording()
        audioRecorder = nil
        state = .sent
        
        // Send audio to iPhone via WatchConnectivity
        if let url = audioFileURL {
            connectivity.sendAudio(fileURL: url)
        }
        
        // Return to idle after delay (will be interrupted if response arrives)
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            if self?.state == .sent {
                self?.state = .idle
            }
        }
    }
    
    private func interruptAndRecord() {
        audioPlayer?.stop()
        audioPlayer = nil
        startRecording()
    }
    
    func onAudioReceived(url: URL) {
        audioPlayer = AudioPlayer()
        audioPlayer?.onPlaybackComplete = { [weak self] in
            DispatchQueue.main.async {
                self?.state = .idle
            }
        }
        audioPlayer?.play(url: url)
        state = .playing
    }
    
    func onPlaybackComplete() {
        state = .idle
    }
}
