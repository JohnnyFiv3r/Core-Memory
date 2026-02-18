// SettingsView.swift — App settings with dark theme
import SwiftUI

struct SettingsView: View {
    @State private var selectedAgent = "Krusty"
    @State private var botToken = ""
    @State private var chatId = ""
    @State private var assemblyAIKey = ""
    @State private var showingTokenSaved = false
    @State private var isSendingTest = false
    @State private var testResult: String?
    
    var body: some View {
        NavigationStack {
            Form {
                Section("Active Agent") {
                    Picker("Agent", selection: $selectedAgent) {
                        Text("Krusty (OpenClaw)").tag("Krusty")
                    }
                    .disabled(true)
                }
                
                Section("AssemblyAI") {
                    SecureField("API Key", text: $assemblyAIKey)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                    
                    Button {
                        saveAssemblyAIKey()
                    } label: {
                        Text("Save API Key")
                            .frame(maxWidth: .infinity)
                            .foregroundColor(assemblyAIKey.isEmpty ? .gray : .white)
                    }
                    .listRowBackground(assemblyAIKey.isEmpty ? Color.gray.opacity(0.2) : Color.blue)
                    .disabled(assemblyAIKey.isEmpty)
                }
                
                Section("Telegram Configuration") {
                    SecureField("Bot Token", text: $botToken)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                    
                    TextField("Chat ID", text: $chatId)
                        .keyboardType(.numberPad)
                    
                    Button {
                        saveCredentials()
                    } label: {
                        Text("Save Credentials")
                            .frame(maxWidth: .infinity)
                            .foregroundColor(botToken.isEmpty || chatId.isEmpty ? .gray : .white)
                    }
                    .listRowBackground(botToken.isEmpty || chatId.isEmpty ? Color.gray.opacity(0.2) : Color.blue)
                    .disabled(botToken.isEmpty || chatId.isEmpty)
                }
                
                Section("Connection") {
                    LabeledContent("Status", value: "Connected")
                    LabeledContent("Transport", value: "Telegram Bot API")
                    
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
                    .listRowBackground(isSendingTest ? Color.gray : Color.blue)
                    .disabled(isSendingTest || botToken.isEmpty || chatId.isEmpty)
                }
                
                Section("Voice") {
                    LabeledContent("Speech-to-Text", value: assemblyAIKey.isEmpty ? "Apple Speech" : "AssemblyAI")
                    LabeledContent("Text-to-Speech", value: "Apple TTS")
                }
                
                Section("About") {
                    LabeledContent("Version", value: "1.0.0")
                    LabeledContent("Agent", value: "Krusty via OpenClaw")
                }
            }
            .navigationTitle("Settings")
            .preferredColorScheme(.dark)
            .alert("Saved", isPresented: $showingTokenSaved) {
                Button("OK") {}
            }
            .onAppear {
                loadCredentials()
            }
        }
    }
    
    private func saveAssemblyAIKey() {
        try? SecureStorage.save(key: "assemblyai_api_key", value: assemblyAIKey)
        showingTokenSaved = true
    }
    
    private func saveCredentials() {
        try? SecureStorage.save(key: "telegram_bot_token", value: botToken)
        try? SecureStorage.save(key: "telegram_chat_id", value: chatId)
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
