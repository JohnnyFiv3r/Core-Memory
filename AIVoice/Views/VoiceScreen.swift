// VoiceScreen.swift — Hands-free voice interface
// Shows transcription from watch audio, countdown to auto-send, agent response.
// Tap transcript to edit before sending. All watch-driven (no mic button on phone).
import SwiftUI

struct VoiceScreen: View {
    @Environment(AppCoordinator.self) private var coordinator
    
    // Edit mode
    @State private var isEditing = false
    @State private var editText = ""
    @FocusState private var editFieldFocused: Bool
    
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
            // Message area
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // Show previous agent response at top
                        if let response = coordinator.agentResponse {
                            agentBubble(text: response, timestamp: coordinator.agentResponseTimestamp)
                                .id("agentResponse")
                        }
                        
                        // Transcribing indicator
                        if coordinator.isTranscribing {
                            transcribingIndicator
                                .id("transcribing")
                        }
                        
                        // User transcript with countdown
                        if let transcript = coordinator.transcribedText,
                           !coordinator.isWaitingForAgent {
                            userBubble(text: transcript, timestamp: coordinator.userTranscriptTimestamp)
                                .id("userTranscript")
                        }
                        
                        // Sent transcript (waiting for agent)
                        if coordinator.isWaitingForAgent {
                            if let transcript = coordinator.lastSentText {
                                sentBubble(text: transcript)
                                    .id("sentMessage")
                            }
                            thinkingIndicator
                                .id("thinking")
                        }
                        
                        // Error
                        if let error = coordinator.lastError {
                            errorBubble(text: error)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: coordinator.agentResponse) {
                    withAnimation { proxy.scrollTo("agentResponse", anchor: .top) }
                }
                .onChange(of: coordinator.isTranscribing) {
                    if coordinator.isTranscribing {
                        withAnimation { proxy.scrollTo("transcribing") }
                    }
                }
                .onChange(of: coordinator.transcribedText) {
                    if coordinator.transcribedText != nil {
                        withAnimation { proxy.scrollTo("userTranscript") }
                        startCountdown()
                    }
                }
            }
            
            Spacer(minLength: 0)
            
            // Bottom bar: countdown or edit
            if isEditing {
                editBar
            } else if countdownRemaining > 0 {
                countdownBar
            }
        }
        .background(ShellPhoneTheme.background)
    }
    
    // MARK: - Agent response bubble (left-aligned, dark card)
    
    private func agentBubble(text: String, timestamp: Date?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                // Agent avatar
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
    
    // MARK: - User transcript bubble (right-aligned, accent color)
    
    private func userBubble(text: String, timestamp: Date?) -> some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text(text)
                .font(.body)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(ShellPhoneTheme.userBubble)
                .cornerRadius(20)
                .onTapGesture {
                    beginEditing(text)
                }
            
            HStack(spacing: 8) {
                if let ts = timestamp {
                    Text(ts.formatted(date: .omitted, time: .shortened))
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.secondaryText)
                }
                if countdownRemaining > 0 {
                    Text("Sending in \(Int(ceil(countdownRemaining)))s...")
                        .font(.caption2)
                        .foregroundColor(ShellPhoneTheme.sending)
                }
            }
            .padding(.trailing, 4)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }
    
    // MARK: - Sent bubble (after countdown, waiting for agent)
    
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
    
    // MARK: - Transcribing pill
    
    private var transcribingIndicator: some View {
        HStack(spacing: 6) {
            // Animated dots
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
    
    // MARK: - Thinking indicator
    
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
    
    // MARK: - Error bubble
    
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
    
    // MARK: - Countdown bar (tap "Edit transcript" to enter edit mode)
    
    private var countdownBar: some View {
        VStack(spacing: 8) {
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
                Button {
                    if let text = coordinator.transcribedText {
                        beginEditing(text)
                    }
                } label: {
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
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(ShellPhoneTheme.topBarBackground)
    }
    
    // MARK: - Edit bar (text field + send button)
    
    private var editBar: some View {
        VStack(spacing: 8) {
            HStack {
                Text("Edit transcript")
                    .font(.caption)
                    .foregroundColor(ShellPhoneTheme.secondaryText)
                Spacer()
                if countdownRemaining > 0 {
                    Text("Auto-send in \(Int(ceil(countdownRemaining)))s")
                        .font(.caption)
                        .foregroundColor(ShellPhoneTheme.accent)
                }
            }
            
            HStack(spacing: 10) {
                TextField("Edit message", text: $editText)
                    .textFieldStyle(.plain)
                    .foregroundColor(.white)
                    .padding(10)
                    .background(ShellPhoneTheme.cardBackground)
                    .cornerRadius(10)
                    .focused($editFieldFocused)
                
                Button {
                    sendEdited()
                } label: {
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
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(ShellPhoneTheme.topBarBackground)
        .onAppear { editFieldFocused = true }
    }
    
    // MARK: - Actions
    
    private func beginEditing(_ text: String) {
        editText = text
        isEditing = true
        // Countdown continues while editing — user can send early or let it auto-send
    }
    
    private func sendEdited() {
        cancelCountdown()
        isEditing = false
        editFieldFocused = false
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
                    // Auto-send: use edited text if editing, otherwise original transcript
                    let textToSend = isEditing ? editText : (coordinator.transcribedText ?? "")
                    isEditing = false
                    editFieldFocused = false
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

// MARK: - Animated dots for transcribing/thinking states

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
