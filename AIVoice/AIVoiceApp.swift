// AIVoiceApp.swift — iOS app entry point
import SwiftUI

@main
struct AIVoiceApp: App {
    @State private var coordinator = AppCoordinator()
    
    var body: some Scene {
        WindowGroup {
            MainTabView()
                .onAppear {
                    coordinator.start()
                }
        }
    }
}
