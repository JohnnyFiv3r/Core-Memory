// ContentView.swift — Main watch view (ultra-minimal, full-screen button)
import SwiftUI

struct ContentView: View {
    @State private var session = VoiceSession()
    
    var body: some View {
        RecordButton(state: session.state, audioLevel: session.audioLevel) {
            session.handleTap()
        }
        .ignoresSafeArea()
    }
}

#Preview {
    ContentView()
}
