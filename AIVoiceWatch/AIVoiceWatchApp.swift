// AIVoiceWatchApp.swift — WatchOS app entry point
import SwiftUI

@main
struct AIVoiceWatchApp: App {
    @State private var session = VoiceSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(session)
        }
    }
}
