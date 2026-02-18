// AppShell.swift — Main shell with hamburger nav and top bar
import SwiftUI

struct AppShell: View {
    @Environment(AppCoordinator.self) private var coordinator
    @State private var drawerOpen = false
    @State private var selectedItem: DrawerItem = .chat

    var body: some View {
        ZStack {
            ShellPhoneTheme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                
                // Content
                Group {
                    switch selectedItem {
                    case .chat:
                        VoiceScreen()
                    case .settings:
                        SettingsView()
                    }
                }
                .frame(maxHeight: .infinity)
            }

            NavigationDrawer(isOpen: $drawerOpen, selectedItem: $selectedItem)
        }
        .preferredColorScheme(.dark)
    }
    
    private var topBar: some View {
        HStack(spacing: 12) {
            // Hamburger
            Button {
                withAnimation(.easeOut(duration: 0.2)) { drawerOpen.toggle() }
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.title3)
                    .foregroundColor(.white)
            }

            // Agent avatar
            Image("AgentAvatar")
                .resizable()
                .scaledToFit()
                .frame(width: 28, height: 28)
                .clipShape(Circle())

            Spacer()

            // Connection status
            HStack(spacing: 6) {
                Circle()
                    .fill(coordinator.connectivity.isWatchReachable ? ShellPhoneTheme.online : .orange)
                    .frame(width: 8, height: 8)
                Text(coordinator.connectivity.isWatchReachable ? "Online" : "Watch not connected")
                    .font(.caption)
                    .foregroundColor(coordinator.connectivity.isWatchReachable ? ShellPhoneTheme.online : .orange)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(ShellPhoneTheme.topBarBackground)
    }
}
