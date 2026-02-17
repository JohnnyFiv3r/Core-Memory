// ChatView.swift — Conversation history view
import SwiftUI

/// Chat history view showing all voice conversations
/// MVP: Uses local ChatManager messages
/// Future: Replace with Stream Chat SwiftUI components
struct ChatView: View {
    @State private var chatManager = ChatManager.shared
    
    var body: some View {
        NavigationStack {
            Group {
                if chatManager.messages.isEmpty {
                    VStack(spacing: 16) {
                        Image(systemName: "waveform.circle")
                            .font(.system(size: 60))
                            .foregroundColor(.secondary)
                        Text("No conversations yet")
                            .font(.headline)
                            .foregroundColor(.secondary)
                        Text("Use your Apple Watch to start a voice conversation with Krusty")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 40)
                    }
                } else {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(spacing: 12) {
                                ForEach(chatManager.messages) { message in
                                    MessageBubble(message: message)
                                        .id(message.id)
                                }
                            }
                            .padding()
                        }
                        .onChange(of: chatManager.messages.count) { _, _ in
                            if let last = chatManager.messages.last {
                                withAnimation {
                                    proxy.scrollTo(last.id, anchor: .bottom)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Krusty")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

/// Individual message bubble
struct MessageBubble: View {
    let message: ChatMessage
    
    var body: some View {
        HStack {
            if message.sender == .user { Spacer() }
            
            VStack(alignment: message.sender == .user ? .trailing : .leading, spacing: 4) {
                Text(message.text)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(message.sender == .user ? Color.blue : Color(.systemGray5))
                    .foregroundColor(message.sender == .user ? .white : .primary)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                
                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            .frame(maxWidth: 280, alignment: message.sender == .user ? .trailing : .leading)
            
            if message.sender == .agent { Spacer() }
        }
    }
}

#Preview {
    ChatView()
}
