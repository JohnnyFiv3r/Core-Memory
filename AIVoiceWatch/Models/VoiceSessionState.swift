// VoiceSessionState.swift — State machine for watch voice session
import SwiftUI
import AVFoundation
import WatchKit

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
        case .idle: return "Clawdio"
        case .recording: return "Recording..."
        case .sent: return "Sent"
        case .playing: return "Playing..."
        }
    }
}

@Observable
class VoiceSession: NSObject, WKExtendedRuntimeSessionDelegate {
    var state: SessionState = .idle
    var audioLevel: Float = 0
    var audioFileURL: URL?

    private var audioRecorder: AudioRecorder?
    private var audioPlayer: AudioPlayer?
    private var meterTimer: Timer?
    private var extendedSession: WKExtendedRuntimeSession?
    let connectivity = WatchConnectivityManager()

    override init() {
        super.init()
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
            break
        case .playing:
            interruptAndRecord()
        }

        #if os(watchOS)
        WKInterfaceDevice.current().play(.click)
        #endif
    }

    // MARK: - Extended Runtime Session

    private func startExtendedSession() {
        guard extendedSession == nil || extendedSession?.state == .invalid else { return }
        let session = WKExtendedRuntimeSession()
        session.delegate = self
        session.start()
        extendedSession = session
        print("VoiceSession: extended runtime session started")
    }

    private func stopExtendedSession() {
        extendedSession?.invalidate()
        extendedSession = nil
    }

    func extendedRuntimeSessionDidStart(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        print("VoiceSession: extended session active")
    }

    func extendedRuntimeSessionWillExpire(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        print("VoiceSession: extended session expiring")
    }

    func extendedRuntimeSession(_ extendedRuntimeSession: WKExtendedRuntimeSession, didInvalidateWith reason: WKExtendedRuntimeSessionInvalidationReason, error: Error?) {
        print("VoiceSession: extended session invalidated: \(reason.rawValue), error: \(error?.localizedDescription ?? "none")")
        extendedSession = nil
    }

    // MARK: - Recording

    private func startRecording() {
        let tempDir = FileManager.default.temporaryDirectory
        let fileURL = tempDir.appendingPathComponent(UUID().uuidString + ".m4a")
        audioFileURL = fileURL

        audioRecorder = AudioRecorder()
        audioRecorder?.startRecording(to: fileURL)
        state = .recording

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

        if let url = audioFileURL {
            connectivity.sendAudio(fileURL: url)
        }

        // Start extended session while waiting for response + playback
        startExtendedSession()

        DispatchQueue.main.asyncAfter(deadline: .now() + 60.0) { [weak self] in
            if self?.state == .sent {
                self?.state = .idle
                self?.stopExtendedSession()
            }
        }
    }

    private func interruptAndRecord() {
        audioPlayer?.stop()
        audioPlayer = nil
        stopExtendedSession()
        startRecording()
    }

    // MARK: - Playback

    func onAudioReceived(url: URL) {
        // Ensure extended session is active for playback
        startExtendedSession()

        audioPlayer = AudioPlayer()
        audioPlayer?.onPlaybackComplete = { [weak self] in
            DispatchQueue.main.async {
                self?.state = .idle
                self?.stopExtendedSession()
            }
        }
        audioPlayer?.play(url: url)
        state = .playing
    }
}
