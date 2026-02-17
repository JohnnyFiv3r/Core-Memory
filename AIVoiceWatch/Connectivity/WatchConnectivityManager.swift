// WatchConnectivityManager.swift — Watch-side WatchConnectivity handler
import Foundation
import WatchConnectivity

@Observable
class WatchConnectivityManager: NSObject, WCSessionDelegate {
    var isPhoneReachable = false
    var onAudioReceived: ((URL) -> Void)?
    var onTransferError: ((Error) -> Void)?
    
    private var wcSession: WCSession?
    
    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
        wcSession = session
    }
    
    func sendAudio(fileURL: URL) {
        guard let session = wcSession,
              session.activationState == .activated else {
            onTransferError?(ConnectivityError.notActivated)
            return
        }
        
        let metadata: [String: Any] = [
            ConnectivityKeys.messageType: AudioMessageType.voiceMessage.rawValue,
            ConnectivityKeys.timestamp: Date().timeIntervalSince1970
        ]
        session.transferFile(fileURL, metadata: metadata)
    }
    
    // MARK: - WCSessionDelegate
    
    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        DispatchQueue.main.async {
            self.isPhoneReachable = session.isReachable
        }
    }
    
    func sessionReachabilityDidChange(_ session: WCSession) {
        DispatchQueue.main.async {
            self.isPhoneReachable = session.isReachable
        }
    }
    
    // Receive TTS audio from iPhone
    func session(_ session: WCSession, didReceive file: WCSessionFile) {
        guard let typeRaw = file.metadata?[ConnectivityKeys.messageType] as? String,
              typeRaw == AudioMessageType.agentResponse.rawValue else { return }
        
        // Copy to permanent location (WC temp file gets deleted)
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".m4a")
        do {
            try FileManager.default.copyItem(at: file.fileURL, to: dest)
            DispatchQueue.main.async {
                self.onAudioReceived?(dest)
            }
        } catch {
            DispatchQueue.main.async {
                self.onTransferError?(error)
            }
        }
    }
    
    // Monitor transfer completion
    func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        if let error {
            DispatchQueue.main.async {
                self.onTransferError?(error)
            }
        }
    }
}

enum ConnectivityError: Error, LocalizedError {
    case notActivated
    case transferFailed(String)
    
    var errorDescription: String? {
        switch self {
        case .notActivated: return "Watch connectivity not activated"
        case .transferFailed(let msg): return "Transfer failed: \(msg)"
        }
    }
}
