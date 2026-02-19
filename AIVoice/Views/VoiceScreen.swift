// VoiceScreen.swift — Chat interface with voice + text input
// Shows full conversation history, text input bar, and voice transcript states.
import SwiftUI

struct VoiceScreen: View {
    @Environment(AppCoordinator.self) private var coordinator

    // Text input
    @State private var inputText = ""
    @FocusState private var inputFocused: Bool

    // Voice edit mode
    @State private var isEditing = false
    @State private var editText = ""

    // Countdown
    @State private var countdownRemaining: Double = 0
    @State private var countdownTotal: Double = 0
    @State private var countdownTimer: Timer?

    private var autoSendDelay: Double {
        let stored = UserDefaults.standard.integer(forKey: "autoSendDelay")
        return Double(stored > 0 ? stored : 2)
    }

    var body: some View {
        VStack(spacing: 0) {
            // MARK: - Message list
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // Chat history
                        ForEach(coordinator.chatManager.messages) { msg in
                            switch msg.sender {
                            case .agent:
                                agentBubble(text: msg.text, timestamp: msg.timestamp)
                            case .user:
                                userHistoryBubble(text: msg.text, timestamp: msg.timestamp)
                            }
                        }

                        // Live states

                        if coordinator.isTranscribing {
                            transcribingIndicator
                                .id("transcribing")
                        }

                        if let transcript = coordinator.transcribedText,
                           !coordinator.isWaitingForAgent {
                            voiceTranscriptBubble(text: transcript, timestamp: coordinator.userTranscriptTimestamp)
                                .id("userTranscript")
                        }

                        if coordinator.isWaitingForAgent {
                            if let transcript = coordinator.lastSentText {
                                sentBubble(text: transcript)
                                    .id("sentMessage")
                            }
                            thinkingIndicator
                                .id("thinking")
                        }

                        if let error = coordinator.lastError {
                            errorBubble(text: error)
                        }

                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onTapGesture { inputFocused = false }
                .onChange(of: coordinator.chatManager.messages.count) {
                    withAnimation { proxy.scrollTo("bottom") }
                }
                .onChange(of: coordinator.isTranscribing) {
                    if coordinator.isTranscribing {
                        withAnimation { proxy.scrollTo("bottom") }
                    }
                }
                .onChange(of: coordinator.transcribedText) {
                    if coordinator.transcribedText != nil {
                        withAnimation { proxy.scrollTo("bottom") }
                        startCountdown()
                    }
                }
                .onChange(of: coordinator.isWaitingForAgent) {
                    withAnimation { proxy.scrollTo("bottom") }
                }
                .onAppear {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                        proxy.scrollTo("bottom", anchor: .bottom)
                    }
                }
            }

            // MARK: - Countdown bar (voice transcript auto-send)
            if countdownRemaining > 0 || isEditing {
                voiceCountdownBar
            }

            // MARK: - Input bar
            inputBar
        }
        .background(ShellPhoneTheme.background)
    }

    // MARK: - Agent bubble

    private func agentBubble(text: String, timestamp: Date?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                Image("AgentAvatar")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 24, height: 24)
                    .clipShape(Circle())
                    .padding(.top, 2)

                Text(text)
                    .font(.body)
                    .foregroundColor(.white)
                    .lineSpacing(4)
            }
            .padding(14)
            .background(ShellPhoneTheme.agentBubble)
            .cornerRadius(16)

            if let ts = timestamp {
                Text(ts.formatted(date: .omitted, time: .shortened))
                    .font(.caption2)
                    .foregroundColor(ShellPhoneTheme.secondaryText)
                    .padding(.leading, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - User history bubble

    private func userHistoryBubble(text: String, timestamp: Date?) -> some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text(text)
                .font(.body)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.userBubble)
                .cornerRadius(20)

            if let ts = timestamp {
                Text(ts.formatted(date: .omitted, time: .shortened))
                    .font(.caption2)
                    .foregroundColor(ShellPhoneTheme.secondaryText)
                    .padding(.trailing, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    // MARK: - Voice transcript bubble (not yet sent, tappable)

    private func voiceTranscriptBubble(text: String, timestamp: Date?) -> some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text(text)
                .font(.body)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.userBubble)
                .cornerRadius(20)
                .onTapGesture { beginEditing(text) }

            HStack(spacing: 8) {
                if let ts = timestamp {
                    Text(ts.formatted(date: .omitted, time: .shortened))
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }
                if countdownRemaining > 0 {
                    Text("Tap to edit")
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.sending)
                }
            }
            .padding(.trailing, 4)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    // MARK: - Sent bubble

    private func sentBubble(text: String) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            Text(text)
                .font(.body)
                .foregroundColor(.white.opacity(0.7))
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.userBubble.opacity(0.6))
                .cornerRadius(20)

            Text("Sent")
                .font(.caption2)
                .foregroundColor(ShellPhoneTheme.secondaryText)
                .padding(.trailing, 4)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    // MARK: - Indicators

    private var transcribingIndicator: some View {
        HStack(spacing: 6) {
            TranscribingDots()
            Text("Transcribing...")
                .font(.caption)
                .foregroundColor(ShellPhoneTheme.accent)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(ShellPhoneTheme.accent.opacity(0.15))
        .cornerRadius(20)
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private var thinkingIndicator: some View {
        HStack(spacing: 10) {
            Image("AgentAvatar")
                .resizable()
                .scaledToFit()
                .frame(width: 20, height: 20)
                .clipShape(Circle())
            TranscribingDots()
        }
        .padding(12)
        .background(ShellPhoneTheme.agentBubble)
        .cornerRadius(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func errorBubble(text: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.orange)
            Text(text)
                .font(.caption)
                .foregroundColor(.orange)
        }
        .padding(12)
        .background(Color.orange.opacity(0.1))
        .cornerRadius(12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Voice countdown bar (above input bar)

    private var voiceCountdownBar: some View {
        VStack(spacing: 6) {
            if countdownRemaining > 0 && !isEditing {
                // Progress bar
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Color.white.opacity(0.1))
                            .frame(height: 3)
                        RoundedRectangle(cornerRadius: 2)
                            .fill(ShellPhoneTheme.accent)
                            .frame(width: geo.size.width * (countdownTotal > 0 ? (1 - countdownRemaining / countdownTotal) : 0), height: 3)
                            .animation(.linear(duration: 0.1), value: countdownRemaining)
                    }
                }
                .frame(height: 3)

                HStack {
                    Button { beginEditingTranscript() } label: {
                        Text("Edit transcript")
                            .font(.caption)
                            .foregroundColor(ShellPhoneTheme.secondaryText)
                    }
                    Spacer()
                    Text("Auto-send in \(Int(ceil(countdownRemaining)))s")
                        .font(.caption)
                        .foregroundColor(ShellPhoneTheme.accent)
                }
            }

            if isEditing {
                HStack(spacing: 10) {
                    TextField("Edit transcript", text: $editText)
                        .textFieldStyle(.plain)
                        .foregroundColor(.white)
                        .padding(10)
                        .background(ShellPhoneTheme.cardBackground)
                        .cornerRadius(10)

                    Button { sendEdited() } label: {
                        Text("Send")
                            .font(.subheadline.bold())
                            .foregroundColor(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            .background(ShellPhoneTheme.accent)
                            .cornerRadius(20)
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(ShellPhoneTheme.topBarBackground)
    }

    // MARK: - Text input bar

    private var inputBar: some View {
        HStack(spacing: 10) {
            TextField("Message", text: $inputText)
                .textFieldStyle(.plain)
                .foregroundColor(.white)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.cardBackground)
                .cornerRadius(22)
                .focused($inputFocused)
                .onSubmit { sendTextMessage() }

            if !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button { sendTextMessage() } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 32))
                        .foregroundColor(ShellPhoneTheme.accent)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(ShellPhoneTheme.topBarBackground)
    }

    // MARK: - Actions

    private func sendTextMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        inputText = ""
        inputFocused = false
        coordinator.sendTranscript(text)
    }

    private func beginEditingTranscript() {
        if let text = coordinator.transcribedText {
            beginEditing(text)
        }
    }

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
        let delay = autoSendDelay
        countdownTotal = delay
        countdownRemaining = delay

        countdownTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
            Task { @MainActor in
                countdownRemaining -= 0.1
                if countdownRemaining <= 0 {
                    cancelCountdown()
                    let textToSend = isEditing ? editText : (coordinator.transcribedText ?? "")
                    isEditing = false
                    let trimmed = textToSend.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !trimmed.isEmpty {
                        coordinator.sendTranscript(trimmed)
                    }
                }
            }
        }
    }

    private func cancelCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        countdownRemaining = 0
        countdownTotal = 0
    }
}

// MARK: - Animated dots

struct TranscribingDots: View {
    @State private var phase = 0
    let timer = Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(ShellPhoneTheme.accent)
                    .frame(width: 6, height: 6)
                    .scaleEffect(phase == i ? 1.3 : 0.8)
                    .opacity(phase == i ? 1.0 : 0.4)
                    .animation(.easeInOut(duration: 0.3), value: phase)
            }
        }
        .onReceive(timer) { _ in
            phase = (phase + 1) % 3
        }
    }
}
