// VoiceScreen.swift — Dark voice chat interface (watch-driven, no mic button)
import SwiftUI

struct VoiceScreen: View {
    @Environment(AppCoordinator.self) private var coordinator

    @State private var isEditing = false
    @State private var editText = ""
    @State private var countdownSeconds = 0
    @State private var countdownTimer: Timer?

    private var autoSendDelay: Int {
        UserDefaults.standard.integer(forKey: "autoSendDelay").clamped(to: 1...10, default: 2)
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // Agent response
                        if let response = coordinator.agentResponse,
                           let ts = coordinator.agentResponseTimestamp {
                            agentBubble(text: response, timestamp: ts)
                                .id("agent")
                        }

                        // Transcribing indicator
                        if coordinator.isTranscribing {
                            transcribingPill()
                        }

                        // User transcript bubble
                        if let text = coordinator.transcribedText,
                           let ts = coordinator.userTranscriptTimestamp {
                            userBubble(text: text, timestamp: ts)
                                .id("user")
                                .onTapGesture { beginEditing(text) }
                        }

                        // Waiting for agent
                        if coordinator.isWaitingForAgent {
                            thinkingIndicator()
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: coordinator.agentResponse) {
                    withAnimation { proxy.scrollTo("agent", anchor: .bottom) }
                }
            }

            Spacer(minLength: 0)

            // Countdown label
            if countdownSeconds > 0 && !isEditing {
                Text("Sending in \(countdownSeconds)s...")
                    .font(.caption)
                    .foregroundColor(ShellPhoneTheme.accent)
                    .padding(.bottom, 4)
            }

            // Edit bar
            if isEditing {
                editBar()
            }
        }
        .background(ShellPhoneTheme.background)
        .onChange(of: coordinator.transcribedText) {
            if coordinator.transcribedText != nil {
                startCountdown()
            }
        }
    }

    // MARK: - Bubbles

    private func agentBubble(text: String, timestamp: Date) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(text)
                .font(.title3)
                .foregroundColor(ShellPhoneTheme.primaryText)
                .padding(14)
                .background(ShellPhoneTheme.cardBackground)
                .cornerRadius(16)

            Text(timestamp.formatted(date: .omitted, time: .shortened))
                .font(.caption2)
                .foregroundColor(ShellPhoneTheme.secondaryText)
                .padding(.leading, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func userBubble(text: String, timestamp: Date) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            Text(text)
                .font(.body)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.accent)
                .cornerRadius(20)

            Text(timestamp.formatted(date: .omitted, time: .shortened))
                .font(.caption2)
                .foregroundColor(ShellPhoneTheme.secondaryText)
                .padding(.trailing, 4)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private func transcribingPill() -> some View {
        HStack(spacing: 6) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(ShellPhoneTheme.accent)
                    .frame(width: 6, height: 6)
                    .opacity(0.6)
            }
            Text("Transcribing...")
                .font(.caption)
                .foregroundColor(ShellPhoneTheme.accent)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(ShellPhoneTheme.cardBackground)
        .cornerRadius(20)
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private func thinkingIndicator() -> some View {
        HStack(spacing: 8) {
            ProgressView()
                .tint(ShellPhoneTheme.secondaryText)
            Text("Thinking...")
                .font(.caption)
                .foregroundColor(ShellPhoneTheme.secondaryText)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Edit Bar

    private func editBar() -> some View {
        VStack(spacing: 6) {
            Text("Edit transcript")
                .font(.caption)
                .foregroundColor(ShellPhoneTheme.secondaryText)

            HStack(spacing: 10) {
                TextField("Edit message", text: $editText)
                    .textFieldStyle(.plain)
                    .foregroundColor(.white)
                    .padding(10)
                    .background(ShellPhoneTheme.cardBackground)
                    .cornerRadius(10)

                Button { sendEdited() } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                        .frame(width: 36, height: 36)
                        .background(ShellPhoneTheme.accent)
                        .clipShape(Circle())
                }
            }

            if countdownSeconds > 0 {
                Text("Auto-send in \(countdownSeconds)s")
                    .font(.caption2)
                    .foregroundColor(ShellPhoneTheme.accent)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(ShellPhoneTheme.drawerBackground)
    }

    // MARK: - Actions

    private func beginEditing(_ text: String) {
        editText = text
        isEditing = true
    }

    private func sendEdited() {
        cancelCountdown()
        isEditing = false
        let text = editText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        coordinator.sendTranscript(text)
    }

    private func startCountdown() {
        cancelCountdown()
        countdownSeconds = autoSendDelay
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            Task { @MainActor in
                countdownSeconds -= 1
                if countdownSeconds <= 0 {
                    cancelCountdown()
                    if let text = coordinator.transcribedText {
                        coordinator.sendTranscript(text)
                    }
                }
            }
        }
    }

    private func cancelCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        countdownSeconds = 0
    }
}

private extension Int {
    func clamped(to range: ClosedRange<Int>, default defaultVal: Int) -> Int {
        self == 0 ? defaultVal : min(max(self, range.lowerBound), range.upperBound)
    }
}
