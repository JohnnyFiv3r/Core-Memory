// RecordButton.swift — Large circular button for watch voice UI
import SwiftUI

struct RecordButton: View {
    let state: SessionState
    let action: () -> Void
    
    @State private var isPulsing = false
    
    var body: some View {
        GeometryReader { geometry in
            let size = min(geometry.size.width, geometry.size.height) * 0.75
            
            VStack {
                Spacer()
                
                Button(action: action) {
                    ZStack {
                        // Glow effect for recording state
                        if state == .recording {
                            Circle()
                                .fill(state.color.opacity(0.3))
                                .frame(width: size * 1.2, height: size * 1.2)
                                .scaleEffect(isPulsing ? 1.1 : 0.9)
                                .animation(
                                    .easeInOut(duration: 1.0).repeatForever(autoreverses: true),
                                    value: isPulsing
                                )
                        }
                        
                        // Main circle
                        Circle()
                            .fill(state.color)
                            .frame(width: size, height: size)
                        
                        // Icon
                        Image(systemName: state.icon)
                            .font(.system(size: size * 0.35))
                            .foregroundColor(.white)
                    }
                }
                .buttonStyle(.plain)
                
                Text(state.label)
                    .font(.caption)
                    .foregroundColor(state.color)
                    .padding(.top, 4)
                
                // Animated dots for recording
                if state == .recording {
                    HStack(spacing: 4) {
                        ForEach(0..<3, id: \.self) { i in
                            Circle()
                                .fill(state.color)
                                .frame(width: 4, height: 4)
                                .opacity(isPulsing ? 1.0 : 0.3)
                                .animation(
                                    .easeInOut(duration: 0.6)
                                        .repeatForever(autoreverses: true)
                                        .delay(Double(i) * 0.2),
                                    value: isPulsing
                                )
                        }
                    }
                }
                
                Spacer()
            }
            .frame(maxWidth: .infinity)
        }
        .onAppear { isPulsing = true }
        .onChange(of: state) { _, _ in isPulsing = true }
    }
}
