// ContentView.swift — Main watch view (ultra-minimal, full-screen button)
import SwiftUI

struct ContentView: View {
    @Environment(VoiceSession.self) private var session

    var body: some View {
        RecordButton(state: session.state, audioLevel: session.audioLevel) {
            session.handleTap()
        }
        .ignoresSafeArea()
    }
}
