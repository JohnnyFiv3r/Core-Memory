// AIVoiceApp.swift — iOS app entry point
import SwiftUI

@main
struct AIVoiceApp: App {
    @State private var coordinator = AppCoordinator()

    var body: some Scene {
        WindowGroup {
            AppShell()
                .environment(coordinator)
                .onAppear {
                    coordinator.start()
                }
        }
    }
}
