// MainTabView.swift — Main iPhone app interface with Voice and Settings tabs
import SwiftUI

struct MainTabView: View {
    var body: some View {
        TabView {
            VoiceScreen()
                .tabItem {
                    Label("Voice", systemImage: "waveform")
                }
            
            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
        }
        .preferredColorScheme(.dark)
    }
}

#Preview {
    MainTabView()
}
