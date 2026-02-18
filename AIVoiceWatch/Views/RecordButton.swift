// RecordButton.swift — Full-screen circular button for watch voice UI
import SwiftUI

struct RecordButton: View {
    let state: SessionState
    let audioLevel: Float
    let action: () -> Void
    
    var body: some View {
        GeometryReader { geometry in
            let size = min(geometry.size.width, geometry.size.height) * 0.85
            let glowScale = state == .recording ? 1.0 + CGFloat(audioLevel) * 0.35 : 1.0
            
            Button(action: action) {
                ZStack {
                    // Glow ring
                    Circle()
                        .fill(state.color.opacity(0.25))
                        .frame(width: size * 1.15, height: size * 1.15)
                        .scaleEffect(glowScale)
                        .opacity(state == .recording ? 1 : 0)
                        .animation(.easeOut(duration: 0.08), value: audioLevel)
                    
                    // Main circle
                    Circle()
                        .fill(state.color)
                        .frame(width: size, height: size)
                    
                    // Icon
                    Image(systemName: state.icon)
                        .font(.system(size: size * 0.35))
                        .foregroundColor(.white)
                }
                .frame(width: size * 1.15, height: size * 1.15)
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
