// PhoneConnectivityManager.swift — iPhone-side WatchConnectivity handler
import Foundation
import WatchConnectivity

@Observable
class PhoneConnectivityManager: NSObject, WCSessionDelegate {
    var isWatchReachable = false
    var isWatchPaired = false
    var onVoiceMessageReceived: ((URL) -> Void)?
    var onTransferError: ((Error) -> Void)?
    
    private var wcSession: WCSession?
    
    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
        wcSession = session
    }
    
    func sendTTSAudio(fileURL: URL, responseText: String) {
        guard let session = wcSession,
              session.activationState == .activated else {
            onTransferError?(ConnectivityError.notActivated)
            return
        }
        
        let metadata: [String: Any] = [
            ConnectivityKeys.messageType: AudioMessageType.agentResponse.rawValue,
            ConnectivityKeys.timestamp: Date().timeIntervalSince1970,
            ConnectivityKeys.responseText: responseText
        ]
        session.transferFile(fileURL, metadata: metadata)
    }
    
    // MARK: - WCSessionDelegate
    
    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        DispatchQueue.main.async {
            self.isWatchReachable = session.isReachable
            self.isWatchPaired = session.isPaired
        }
    }
    
    // Required on iOS
    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) {
        // Reactivate for watch switching
        session.activate()
    }
    
    func sessionReachabilityDidChange(_ session: WCSession) {
        DispatchQueue.main.async {
            self.isWatchReachable = session.isReachable
            self.isWatchPaired = session.isPaired
        }
    }
    
    // Receive voice recordings from watch
    func session(_ session: WCSession, didReceive file: WCSessionFile) {
        guard let typeRaw = file.metadata?[ConnectivityKeys.messageType] as? String,
              typeRaw == AudioMessageType.voiceMessage.rawValue else { return }
        
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".m4a")
        do {
            try FileManager.default.copyItem(at: file.fileURL, to: dest)
            DispatchQueue.main.async {
                self.onVoiceMessageReceived?(dest)
            }
        } catch {
            DispatchQueue.main.async {
                self.onTransferError?(error)
            }
        }
    }
    
    // Monitor transfer completion — clean up temp files
    func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        // Delete the source file after transfer completes (success or fail)
        let fileURL = fileTransfer.file.fileURL
        try? FileManager.default.removeItem(at: fileURL)

        if let error {
            DispatchQueue.main.async {
                self.onTransferError?(error)
            }
        }
    }
}
