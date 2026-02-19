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

            // Watch status
            HStack(spacing: 6) {
                Circle()
                    .fill(watchStatusColor)
                    .frame(width: 8, height: 8)
                Text(watchStatusText)
                    .font(.caption)
                    .foregroundColor(watchStatusColor)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(ShellPhoneTheme.topBarBackground)
    }

    private var watchStatusColor: Color {
        if coordinator.connectivity.isWatchReachable {
            return ShellPhoneTheme.online
        } else if coordinator.connectivity.isWatchPaired {
            return .yellow
        } else {
            return .orange
        }
    }

    private var watchStatusText: String {
        if coordinator.connectivity.isWatchReachable {
            return "Watch Active"
        } else if coordinator.connectivity.isWatchPaired {
            return "Watch Paired"
        } else {
            return "No Watch"
        }
    }
}
