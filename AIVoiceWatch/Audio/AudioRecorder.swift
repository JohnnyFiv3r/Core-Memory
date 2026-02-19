// AudioRecorder.swift — AVAudioRecorder wrapper for AAC capture on WatchOS
import AVFoundation

class AudioRecorder: NSObject, AVAudioRecorderDelegate {
    private var recorder: AVAudioRecorder?
    
    private let recordSettings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
        AVSampleRateKey: 16000,
        AVNumberOfChannelsKey: 1,
        AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue
    ]
    
    func startRecording(to url: URL) {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default)
            try session.setActive(true)
            
            recorder = try AVAudioRecorder(url: url, settings: recordSettings)
            recorder?.delegate = self
            recorder?.isMeteringEnabled = true
            recorder?.prepareToRecord()
            recorder?.record()
        } catch {
            print("AudioRecorder: Failed to start recording: \(error)")
        }
    }
    
    func stopRecording() {
        recorder?.stop()
        recorder = nil
        
        let session = AVAudioSession.sharedInstance()
        try? session.setActive(false)
    }
    
    /// Returns normalized audio level 0.0–1.0
    func currentLevel() -> Float {
        guard let recorder = recorder, recorder.isRecording else { return 0 }
        recorder.updateMeters()
        let dB = recorder.averagePower(forChannel: 0)  // -160 to 0
        // Normalize: -50dB and below = 0, 0dB = 1
        let minDB: Float = -50
        let clamped = max(minDB, min(dB, 0))
        return (clamped - minDB) / (0 - minDB)
    }
    
    var isRecording: Bool {
        recorder?.isRecording ?? false
    }
}
