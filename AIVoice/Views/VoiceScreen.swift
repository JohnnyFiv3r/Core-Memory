// VoiceScreen.swift — Dark hands-free voice interface
import SwiftUI

struct VoiceScreen: View {
    @State private var chatManager = ChatManager.shared
    @State private var isRecording = false
    @State private var transcript = ""
    @State private var isEditing = false
    @State private var agentResponse = ""
    @State private var countdownProgress: CGFloat = 0
    @State private var countdownTimer: Timer?
    @State private var showCountdown = false
    
    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            
            VStack(spacing: 0) {
                Spacer()
                
                // Agent response area
                if !agentResponse.isEmpty {
                    ScrollView {
                        Text(agentResponse)
                            .font(.title2)
                            .foregroundColor(.white)
                            .multilineTextAlignment(.leading)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 24)
                    }
                    .frame(maxHeight: .infinity)
                }
                
                // Transcript area
                if !transcript.isEmpty && agentResponse.isEmpty {
                    Spacer()
                    
                    if isEditing {
                        TextField("Edit message", text: $transcript)
                            .font(.title)
                            .foregroundColor(.gray)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 24)
                            .onSubmit {
                                sendMessage()
                            }
                    } else {
                        Text(""\(transcript)"")
                            .font(.title)
                            .foregroundColor(.gray)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 24)
                            .onTapGesture {
                                isEditing = true
                                cancelCountdown()
                            }
                    }
                    
                    // Countdown bar
                    if showCountdown {
                        GeometryReader { geo in
                            Rectangle()
                                .fill(Color.accentColor)
                                .frame(width: geo.size.width * countdownProgress, height: 3)
                                .animation(.linear(duration: 0.05), value: countdownProgress)
                        }
                        .frame(height: 3)
                        .padding(.horizontal, 24)
                        .padding(.top, 12)
                    }
                    
                    Spacer()
                }
                
                if transcript.isEmpty && agentResponse.isEmpty {
                    Spacer()
                    Text("Tap to speak")
                        .font(.title2)
                        .foregroundColor(.gray.opacity(0.5))
                    Spacer()
                }
                
                // Mic button
                Button(action: micTapped) {
                    ZStack {
                        Circle()
                            .fill(Color.accentColor)
                            .frame(width: 72, height: 72)
                            .shadow(color: isRecording ? Color.accentColor.opacity(0.6) : .clear, radius: isRecording ? 20 : 0)
                        
                        Image(systemName: isRecording ? "stop.fill" : "mic.fill")
                            .font(.system(size: 28))
                            .foregroundColor(.white)
                    }
                }
                .padding(.bottom, 32)
            }
        }
        .preferredColorScheme(.dark)
    }
    
    private func micTapped() {
        if isRecording {
            stopRecording()
        } else {
            startRecording()
        }
    }
    
    private func startRecording() {
        isRecording = true
        agentResponse = ""
        transcript = ""
        isEditing = false
        cancelCountdown()
    }
    
    private func stopRecording() {
        isRecording = false
        // Simulate transcript from recording (real impl would use VoicePipeline)
        if transcript.isEmpty {
            transcript = "..."
        }
        startCountdown()
    }
    
    private func startCountdown() {
        showCountdown = true
        countdownProgress = 0
        let start = Date()
        let duration: TimeInterval = 2.0
        
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { timer in
            let elapsed = Date().timeIntervalSince(start)
            countdownProgress = min(1.0, CGFloat(elapsed / duration))
            if elapsed >= duration {
                timer.invalidate()
                sendMessage()
            }
        }
    }
    
    private func cancelCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        showCountdown = false
        countdownProgress = 0
    }
    
    private func sendMessage() {
        cancelCountdown()
        let message = transcript
        guard !message.isEmpty else { return }
        
        chatManager.recordSentMessage(text: message)
        
        // Placeholder: in real app, AppCoordinator handles agent call
        agentResponse = "Processing..."
        transcript = ""
        isEditing = false
    }
}

#Preview {
    VoiceScreen()
}
