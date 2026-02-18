// SettingsView.swift — ShellPhone settings
import SwiftUI

struct SettingsView: View {
    @Environment(AppCoordinator.self) private var coordinator
    
    @State private var botToken = ""
    @State private var chatId = ""
    @State private var assemblyAIKey = ""
    @State private var showingSaved = false
    @State private var isSendingTest = false
    @State private var testResult: String?
    @State private var autoSendDelay: Int = {
        let stored = UserDefaults.standard.integer(forKey: "autoSendDelay")
        return stored > 0 ? stored : 2
    }()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Settings")
                    .font(.largeTitle.bold())
                    .foregroundColor(.white)
                    .padding(.top, 8)
                
                // MARK: - Agent
                settingsSection("AGENT") {
                    settingsRow {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Active Agent")
                                    .foregroundColor(.white)
                                Text(coordinator.agentManager.activeAgent?.agentName ?? "Not configured")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                            }
                            Spacer()
                            Text("Telegram")
                                .font(.caption)
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                }
                
                // MARK: - API Configuration
                settingsSection("API CONFIGURATION") {
                    VStack(spacing: 1) {
                        settingsRow {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Telegram Bot Token")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                                SecureField("Bot token", text: $botToken)
                                    .textContentType(.password)
                                    .autocorrectionDisabled()
                                    .foregroundColor(.white)
                            }
                        }
                        
                        settingsRow {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Chat ID")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                                TextField("Chat ID", text: $chatId)
                                    .keyboardType(.numberPad)
                                    .foregroundColor(.white)
                            }
                        }
                        
                        settingsRow {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("AssemblyAI API Key")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                                SecureField("API key", text: $assemblyAIKey)
                                    .textContentType(.password)
                                    .autocorrectionDisabled()
                                    .foregroundColor(.white)
                            }
                        }
                    }
                    
                    Text("Your API keys are stored locally in the Keychain and never shared.")
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                        .padding(.top, 4)
                    
                    HStack(spacing: 12) {
                        Button {
                            saveCredentials()
                        } label: {
                            Text("Save")
                                .font(.subheadline.bold())
                                .foregroundColor(.white)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(ShellPhoneTheme.accent)
                                .cornerRadius(10)
                        }
                        
                        Button {
                            sendTestMessage()
                        } label: {
                            HStack(spacing: 4) {
                                if isSendingTest {
                                    ProgressView()
                                        .scaleEffect(0.7)
                                }
                                Text(testResult ?? "Test")
                                    .font(.subheadline.bold())
                            }
                            .foregroundColor(testResultColor)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .background(ShellPhoneTheme.cardBackground)
                            .cornerRadius(10)
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(ShellPhoneTheme.accent.opacity(0.3), lineWidth: 1)
                            )
                        }
                        .disabled(isSendingTest || botToken.isEmpty || chatId.isEmpty)
                    }
                    .padding(.top, 8)
                }
                
                // MARK: - Voice
                settingsSection("VOICE") {
                    settingsRow {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Auto-send Delay")
                                    .foregroundColor(.white)
                                Text("Time to edit before sending")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                            }
                            Spacer()
                            Stepper("\(autoSendDelay) seconds", value: $autoSendDelay, in: 1...10)
                                .labelsHidden()
                            Text("\(autoSendDelay)s")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                                .frame(width: 30)
                        }
                        .onChange(of: autoSendDelay) {
                            UserDefaults.standard.set(autoSendDelay, forKey: "autoSendDelay")
                        }
                    }
                    
                    settingsRow {
                        HStack {
                            Text("Language")
                                .foregroundColor(.white)
                            Spacer()
                            Text("English (US)")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                    
                    settingsRow {
                        HStack {
                            Text("Speech-to-Text")
                                .foregroundColor(.white)
                            Spacer()
                            Text(assemblyAIKey.isEmpty ? "Apple Speech" : "AssemblyAI")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                }
                
                // MARK: - About
                settingsSection("ABOUT") {
                    settingsRow {
                        HStack {
                            Text("Version")
                                .foregroundColor(.white)
                            Spacer()
                            Text("1.0.0")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                    settingsRow {
                        HStack {
                            Text("Build")
                                .foregroundColor(.white)
                            Spacer()
                            Text("2026.02.18")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                }
                
                Spacer(minLength: 40)
            }
            .padding(.horizontal, 16)
        }
        .background(ShellPhoneTheme.background)
        .alert("Credentials Saved", isPresented: $showingSaved) {
            Button("OK") {
                coordinator.reloadAgent()
            }
        }
        .onAppear { loadCredentials() }
    }
    
    // MARK: - Helpers
    
    private var testResultColor: Color {
        guard let result = testResult else { return .white }
        if result.hasPrefix("✅") { return .green }
        if result.hasPrefix("❌") { return .red }
        return .white
    }
    
    private func settingsSection(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.bold())
                .foregroundColor(ShellPhoneTheme.secondaryText)
                .tracking(1)
            content()
        }
    }
    
    private func settingsRow<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(ShellPhoneTheme.cardBackground)
            .cornerRadius(10)
    }

    private func saveCredentials() {
        try? SecureStorage.save(key: "telegram_bot_token", value: botToken)
        try? SecureStorage.save(key: "telegram_chat_id", value: chatId)
        if !assemblyAIKey.isEmpty {
            try? SecureStorage.save(key: "assemblyai_api_key", value: assemblyAIKey)
        }
        showingSaved = true
    }

    private func loadCredentials() {
        botToken = (try? SecureStorage.load(key: "telegram_bot_token")) ?? ""
        chatId = (try? SecureStorage.load(key: "telegram_chat_id")) ?? ""
        assemblyAIKey = (try? SecureStorage.load(key: "assemblyai_api_key")) ?? ""
    }

    private func sendTestMessage() {
        isSendingTest = true
        testResult = nil

        Task {
            do {
                let api = TelegramAPI(botToken: botToken)
                let response = try await api.sendMessage(
                    chatId: chatId,
                    text: "🧪 ShellPhone test — Telegram integration working!"
                )
                await MainActor.run {
                    testResult = "✅ Sent"
                    isSendingTest = false
                }
            } catch {
                await MainActor.run {
                    testResult = "❌ Failed"
                    isSendingTest = false
                }
            }
        }
    }
}
