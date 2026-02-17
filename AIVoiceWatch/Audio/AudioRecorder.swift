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
            try session.setCategory(.record, mode: .default)
            try session.setActive(true)
            
            recorder = try AVAudioRecorder(url: url, settings: recordSettings)
            recorder?.delegate = self
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
    
    var isRecording: Bool {
        recorder?.isRecording ?? false
    }
}
