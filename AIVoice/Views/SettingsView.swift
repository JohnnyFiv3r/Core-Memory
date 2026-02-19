// SettingsView.swift — Clawdio settings
import SwiftUI

struct SettingsView: View {
    @Environment(AppCoordinator.self) private var coordinator

    @State private var gatewayURL = ""
    @State private var gatewayToken = ""
    @State private var assemblyAIKey = ""
    @State private var showingSaved = false
    @State private var isTesting = false
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
                            Text(connectionMode)
                                .font(.caption)
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                }

                // MARK: - OpenClaw Gateway
                settingsSection("OPENCLAW GATEWAY") {
                    VStack(spacing: 1) {
                        settingsRow {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Gateway URL")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                                TextField("http://192.168.1.x:18789", text: $gatewayURL)
                                    .keyboardType(.URL)
                                    .autocorrectionDisabled()
                                    .textInputAutocapitalization(.never)
                                    .foregroundColor(.white)
                            }
                        }

                        settingsRow {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Gateway Token")
                                    .font(.caption)
                                    .foregroundColor(ShellPhoneTheme.secondaryText)
                                SecureField("Token", text: $gatewayToken)
                                    .textContentType(.password)
                                    .autocorrectionDisabled()
                                    .foregroundColor(.white)
                            }
                        }
                    }

                    Text("Connect directly to your OpenClaw gateway on the local network.")
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                        .padding(.top, 4)
                }

                // MARK: - Speech
                settingsSection("SPEECH") {
                    settingsRow {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("AssemblyAI API Key")
                                .font(.caption)
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                            SecureField("API key (optional)", text: $assemblyAIKey)
                                .textContentType(.password)
                                .autocorrectionDisabled()
                                .foregroundColor(.white)
                        }
                    }

                    Text(assemblyAIKey.isEmpty
                         ? "Using Apple Speech. Add AssemblyAI key for better accuracy."
                         : "Using AssemblyAI for speech-to-text.")
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                        .padding(.top, 4)
                }

                // MARK: - Save / Test
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
                        testConnection()
                    } label: {
                        HStack(spacing: 4) {
                            if isTesting {
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
                    .disabled(isTesting || gatewayURL.isEmpty || gatewayToken.isEmpty)
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
                            Text("2026.02.19")
                                .foregroundColor(ShellPhoneTheme.secondaryText)
                        }
                    }
                }

                Spacer(minLength: 40)
            }
            .padding(.horizontal, 16)
        }
        .background(ShellPhoneTheme.background)
        .alert("Settings Saved", isPresented: $showingSaved) {
            Button("OK") {
                coordinator.reloadAgent()
            }
        }
        .onAppear { loadCredentials() }
    }

    // MARK: - Helpers

    private var connectionMode: String {
        if !gatewayURL.isEmpty && !gatewayToken.isEmpty {
            return "OpenClaw"
        }
        return "Not configured"
    }

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
        try? SecureStorage.save(key: "openclaw_gateway_url", value: gatewayURL)
        try? SecureStorage.save(key: "openclaw_gateway_token", value: gatewayToken)
        if !assemblyAIKey.isEmpty {
            try? SecureStorage.save(key: "assemblyai_api_key", value: assemblyAIKey)
        }
        showingSaved = true
    }

    private func loadCredentials() {
        gatewayURL = (try? SecureStorage.load(key: "openclaw_gateway_url")) ?? ""
        gatewayToken = (try? SecureStorage.load(key: "openclaw_gateway_token")) ?? ""
        assemblyAIKey = (try? SecureStorage.load(key: "assemblyai_api_key")) ?? ""
    }

    private func testConnection() {
        isTesting = true
        testResult = nil

        Task {
            do {
                let config = AgentConfiguration(
                    name: "Test",
                    type: .openclaw,
                    config: [
                        "gatewayURL": gatewayURL,
                        "gatewayToken": gatewayToken,
                        "agentId": "main",
                        "userId": "clawdio-test"
                    ]
                )
                let service = OpenClawAgentService(config: config)
                let response = try await service.send(message: "ping")
                await MainActor.run {
                    testResult = "✅ Connected"
                    isTesting = false
                }
            } catch {
                await MainActor.run {
                    testResult = "❌ \(error.localizedDescription.prefix(30))"
                    isTesting = false
                }
            }
        }
    }
}
