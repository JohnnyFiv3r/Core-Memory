// ContentView.swift — Main watch view
import SwiftUI

struct ContentView: View {
    @State private var session = VoiceSession()
    
    var body: some View {
        VStack(spacing: 0) {
            Text("AI Voice")
                .font(.headline)
                .padding(.top, 4)
            
            RecordButton(state: session.state) {
                session.handleTap()
            }
        }
    }
}

#Preview {
    ContentView()
}
