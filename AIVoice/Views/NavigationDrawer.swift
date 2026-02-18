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

    var body: some View {
        ZStack(alignment: .leading) {
            // Dark overlay
            if isOpen {
                Color.black.opacity(0.5)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation { isOpen = false } }
            }

            // Drawer
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    Text("ShellPhone")
                        .font(.title2.bold())
                        .foregroundColor(ShellPhoneTheme.primaryText)
                        .padding(.top, 60)
                        .padding(.bottom, 32)
                        .padding(.horizontal, 24)

                    ForEach(DrawerItem.allCases, id: \.self) { item in
                        Button {
                            selectedItem = item
                            withAnimation { isOpen = false }
                        } label: {
                            HStack(spacing: 14) {
                                Image(systemName: item.icon)
                                    .font(.system(size: 20))
                                Text(item.rawValue)
                                    .font(.body)
                            }
                            .foregroundColor(selectedItem == item ? ShellPhoneTheme.accent : ShellPhoneTheme.primaryText)
                            .padding(.vertical, 14)
                            .padding(.horizontal, 24)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    Spacer()
                }
                .frame(width: UIScreen.main.bounds.width * 0.7)
                .background(ShellPhoneTheme.drawerBackground)

                Spacer()
            }
            .offset(x: isOpen ? 0 : -UIScreen.main.bounds.width * 0.7)
            .animation(.easeInOut(duration: 0.25), value: isOpen)
        }
    }
}
