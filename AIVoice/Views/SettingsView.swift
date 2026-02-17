// SettingsView.swift — App settings with stubbed agent switching
import SwiftUI

struct SettingsView: View {
    @State private var selectedAgent = "Krusty"
    @State private var botToken = ""
    @State private var chatId = ""
    @State private var showingTokenSaved = false
    
    var body: some View {
        NavigationStack {
            Form {
                Section("Active Agent") {
                    Picker("Agent", selection: $selectedAgent) {
                        Text("Krusty (OpenClaw)").tag("Krusty")
                        // Future agents:
                        // Text("ChatGPT").tag("ChatGPT")
                    }
                    .disabled(true)  // MVP: not selectable yet
                }
                
                Section("Telegram Configuration") {
                    SecureField("Bot Token", text: $botToken)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                    
                    TextField("Chat ID", text: $chatId)
                        .keyboardType(.numberPad)
                    
                    Button("Save Credentials") {
                        saveCredentials()
                    }
                    .disabled(botToken.isEmpty || chatId.isEmpty)
                }
                
                Section("Connection") {
                    LabeledContent("Status", value: "Connected")
                    LabeledContent("Transport", value: "Telegram Bot API")
                }
                
                Section("Voice") {
                    LabeledContent("Speech-to-Text", value: "Apple Speech")
                    LabeledContent("Text-to-Speech", value: "Apple TTS")
                }
                
                Section("About") {
                    LabeledContent("Version", value: "1.0.0")
                    LabeledContent("Agent", value: "Krusty via OpenClaw")
                }
            }
            .navigationTitle("Settings")
            .alert("Credentials Saved", isPresented: $showingTokenSaved) {
                Button("OK") {}
            }
            .onAppear {
                loadCredentials()
            }
        }
    }
    
    private func saveCredentials() {
        try? SecureStorage.save(key: "telegram_bot_token", value: botToken)
        try? SecureStorage.save(key: "telegram_chat_id", value: chatId)
        showingTokenSaved = true
    }
    
    private func loadCredentials() {
        botToken = (try? SecureStorage.load(key: "telegram_bot_token")) ?? ""
        chatId = (try? SecureStorage.load(key: "telegram_chat_id")) ?? ""
    }
}

#Preview {
    SettingsView()
}
