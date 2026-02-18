// AppShell.swift — Main shell replacing MainTabView with hamburger navigation
import SwiftUI

struct AppShell: View {
    @Environment(AppCoordinator.self) private var coordinator
    @State private var drawerOpen = false
    @State private var selectedItem: DrawerItem = .chat

    var body: some View {
        ZStack {
            ShellPhoneTheme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                // Top bar
                HStack(spacing: 12) {
                    Button { withAnimation { drawerOpen.toggle() } } label: {
                        Image(systemName: "line.3.horizontal")
                            .font(.title2)
                            .foregroundColor(.white)
                    }

                    Image(systemName: "person.crop.circle.fill")
                        .font(.system(size: 32))
                        .foregroundColor(ShellPhoneTheme.accent)

                    Spacer()

                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color.green)
                            .frame(width: 8, height: 8)
                        Text("Online")
                            .font(.subheadline)
                            .foregroundColor(.green)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(ShellPhoneTheme.cardBackground)

                // Content
                switch selectedItem {
                case .chat:
                    VoiceScreen()
                case .settings:
                    SettingsView()
                }
            }

            NavigationDrawer(isOpen: $drawerOpen, selectedItem: $selectedItem)
        }
        .preferredColorScheme(.dark)
    }
}
