// NavigationDrawer.swift — Slide-out hamburger drawer
import SwiftUI

enum DrawerItem: String, CaseIterable {
    case chat = "Chat"
    case settings = "Settings"

    var icon: String {
        switch self {
        case .chat: return "message.fill"
        case .settings: return "gear"
        }
    }
}

struct NavigationDrawer: View {
    @Binding var isOpen: Bool
    @Binding var selectedItem: DrawerItem
    
    private let drawerWidth: CGFloat = UIScreen.main.bounds.width * 0.7

    var body: some View {
        ZStack(alignment: .leading) {
            // Scrim
            if isOpen {
                Color.black.opacity(0.5)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { isOpen = false } }
            }

            // Drawer panel
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    // App name
                    Text("Clawdio")
                        .font(.title2.bold())
                        .foregroundColor(.white)
                        .padding(.top, 60)
                        .padding(.bottom, 32)
                        .padding(.horizontal, 24)

                    // Menu items
                    ForEach(DrawerItem.allCases, id: \.self) { item in
                        drawerRow(item)
                    }

                    Spacer()
                }
                .frame(width: drawerWidth)
                .background(ShellPhoneTheme.drawerBackground.ignoresSafeArea())

                Spacer(minLength: 0)
            }
            .offset(x: isOpen ? 0 : -drawerWidth)
            .animation(.easeOut(duration: 0.2), value: isOpen)
        }
    }
    
    private func drawerRow(_ item: DrawerItem) -> some View {
        Button {
            selectedItem = item
            withAnimation(.easeOut(duration: 0.2)) { isOpen = false }
        } label: {
            HStack(spacing: 14) {
                Image(systemName: item.icon)
                    .font(.system(size: 18))
                    .frame(width: 24)
                Text(item.rawValue)
                    .font(.body)
            }
            .foregroundColor(selectedItem == item ? ShellPhoneTheme.accent : .white)
            .padding(.vertical, 14)
            .padding(.horizontal, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selectedItem == item ? ShellPhoneTheme.accent.opacity(0.1) : .clear)
        }
    }
}
