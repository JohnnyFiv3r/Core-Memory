// SettingsView.swift — App settings with dark theme matching Figma spec
import SwiftUI

struct SettingsView: View {
    @State private var botToken = ""
    @State private var chatId = ""
    @State private var assemblyAIKey = ""
    @State private var showingTokenSaved = false
    @State private var isSendingTest = false
    @State private var testResult: String?
    @State private var autoSendDelay = UserDefaults.standard.integer(forKey: "autoSendDelay") == 0 ? 2 : UserDefaults.standard.integer(forKey: "autoSendDelay")

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Text("GPT-4")
                            .foregroundColor(ShellPhoneTheme.primaryText)
                        Spacer()
                        Image(systemName: "chevron.right")
                            .foregroundColor(ShellPhoneTheme.secondaryText)
                    }
                } header: {
                    Text("AI MODEL")
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }

                Section {
                    SecureField("Telegram Bot Token", text: $botToken)
                        .textContentType(.password)
                        .autocorrectionDisabled()

                    TextField("Chat ID", text: $chatId)
                        .keyboardType(.numberPad)

                    SecureField("AssemblyAI API Key", text: $assemblyAIKey)
                        .textContentType(.password)
                        .autocorrectionDisabled()

                    Text("Your API key is stored locally and never shared")
                        .font(.caption)
                        .foregroundColor(ShellPhoneTheme.secondaryText)

                    Button {
                        saveCredentials()
                    } label: {
                        Text("Save Credentials")
                            .frame(maxWidth: .infinity)
                            .foregroundColor(.white)
                    }
                    .listRowBackground(ShellPhoneTheme.accent)

                    Button {
                        sendTestMessage()
                    } label: {
                        HStack {
                            if isSendingTest {
                                ProgressView()
                                    .padding(.trailing, 4)
                            }
                            Text(testResult ?? "Send Test Message")
                                .frame(maxWidth: .infinity)
                                .foregroundColor(testResult != nil ? (testResult!.starts(with: "✅") ? .green : .red) : .white)
                        }
                    }
                    .listRowBackground(isSendingTest ? Color.gray : ShellPhoneTheme.accent)
                    .disabled(isSendingTest || botToken.isEmpty || chatId.isEmpty)
                } header: {
                    Text("API CONFIGURATION")
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }

                Section {
                    Stepper("Auto-send Delay: \(autoSendDelay)s", value: $autoSendDelay, in: 1...10)
                        .foregroundColor(ShellPhoneTheme.primaryText)
                        .onChange(of: autoSendDelay) {
                            UserDefaults.standard.set(autoSendDelay, forKey: "autoSendDelay")
                        }

                    HStack {
                        Text("Language")
                            .foregroundColor(ShellPhoneTheme.primaryText)
                        Spacer()
                        Text("English (US)")
                            .foregroundColor(ShellPhoneTheme.secondaryText)
                    }
                } header: {
                    Text("VOICE")
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }

                Section {
                    LabeledContent("Version", value: "1.0.0")
                    LabeledContent("Build", value: "2026.02.18")
                } header: {
                    Text("ABOUT")
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }
            }
            .navigationTitle("Settings")
            .preferredColorScheme(.dark)
            .alert("Saved", isPresented: $showingTokenSaved) {
                Button("OK") {}
            }
            .onAppear { loadCredentials() }
        }
    }

    private func saveCredentials() {
        try? SecureStorage.save(key: "telegram_bot_token", value: botToken)
        try? SecureStorage.save(key: "telegram_chat_id", value: chatId)
        if !assemblyAIKey.isEmpty {
            try? SecureStorage.save(key: "assemblyai_api_key", value: assemblyAIKey)
        }
        showingTokenSaved = true
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
                    text: "🧪 ShellPhone test message — if you see this, Telegram integration is working!"
                )
                await MainActor.run {
                    testResult = "✅ Sent (msg \(response.message_id))"
                    isSendingTest = false
                }
            } catch {
                await MainActor.run {
                    testResult = "❌ \(error.localizedDescription)"
                    isSendingTest = false
                }
            }
        }
    }
}

#Preview {
    SettingsView()
}
