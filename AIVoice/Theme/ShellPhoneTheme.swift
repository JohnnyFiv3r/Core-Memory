// ShellPhoneTheme.swift — Style constants for ShellPhone UI
import SwiftUI

enum ShellPhoneTheme {
    // Core palette
    static let accent = Color(red: 0.9, green: 0.3, blue: 0.25)
    static let background = Color.black
    static let cardBackground = Color(white: 0.12)
    static let drawerBackground = Color(white: 0.1)
    static let topBarBackground = Color(white: 0.08)
    
    // Text
    static let primaryText = Color.white
    static let secondaryText = Color(white: 0.5)
    
    // Bubbles
    static let agentBubble = Color(white: 0.14)
    static let userBubble = accent
    
    // Status
    static let online = Color.green
    static let sending = accent
}
