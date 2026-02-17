# AI Voice — WatchOS Agent Interface

A voice-first interface to [OpenClaw](https://github.com/openclaw/openclaw) coding agents from Apple Watch.

Tap. Talk. Listen. That's it.

## Architecture

```
Watch: Tap → Record AAC → Tap → Send to iPhone (WatchConnectivity)
   ↓
iPhone: Receive AAC → Apple Speech STT → Text
   ↓
iPhone: Send text to Telegram API (Krusty/OpenClaw agent)
   ↓
Telegram: Agent processes → responds
   ↓
iPhone: Receive response → Apple TTS → AAC
   ↓
iPhone: Send audio to Watch + Store in chat history
   ↓
Watch: Play audio → Return to idle
```

## Features

- **Watch App**: Single big-button UI. Tap to record, tap to send. Audio-only responses with interrupt support.
- **iPhone App**: Stream Chat UI for conversation history. Settings for agent configuration.
- **Voice Pipeline**: Apple Speech (STT) + Apple AVSpeechSynthesizer (TTS). On-device, zero cost.
- **Agent Transport**: Telegram Bot API to OpenClaw/Krusty. No custom backend.
- **Interrupt**: Tap the mic during playback to cut audio and start a new recording immediately.

## Watch States

| State | Color | Icon | Action |
|-------|-------|------|--------|
| Idle | 🔵 Blue | Mic | Tap to record |
| Recording | 🔴 Red | Mic (pulsing) | Tap to send |
| Sent | 🟢 Green | Checkmark | Waiting for response |
| Playing | 🔵 Blue | Speaker | Tap to interrupt + record |

## Requirements

- WatchOS 10+
- iOS 17+
- Xcode 15+
- Apple Watch (real hardware for audio testing)

## Setup

### 1. Generate Xcode Project

Install [XcodeGen](https://github.com/yonaskolb/XcodeGen) and run:

```bash
brew install xcodegen
cd ai-voice-ios
xcodegen generate
open AIVoice.xcodeproj
```

### 2. Configure Telegram

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather) (or use your existing OpenClaw bot)
2. Get the bot token and your chat ID
3. Run the app → Settings → Enter credentials

### 3. Stream Chat (Optional)

The app includes a local chat history out of the box. To enable Stream Chat SDK:

1. Get an API key from [getstream.io](https://getstream.io)
2. Add the SPM dependency: `https://github.com/GetStream/stream-chat-swiftui`
3. Uncomment the Stream Chat code in `ChatConfiguration.swift` and `ChatManager.swift`

## Project Structure

```
AIVoice/
├── AIVoice/                      # iOS app
│   ├── Agents/                   # Agent service layer
│   │   ├── AgentService.swift    # Protocol
│   │   ├── AgentConfiguration.swift
│   │   └── Telegram/             # Telegram Bot API implementation
│   ├── Chat/                     # Stream Chat integration
│   ├── Connectivity/             # WatchConnectivity (phone side)
│   ├── Storage/                  # Keychain wrapper
│   ├── Views/                    # SwiftUI views
│   ├── Voice/                    # STT + TTS pipeline
│   ├── AppCoordinator.swift      # Central coordinator
│   └── AIVoiceApp.swift          # Entry point
├── AIVoiceWatch/                 # WatchOS app
│   ├── Audio/                    # Recording + playback
│   ├── Connectivity/             # WatchConnectivity (watch side)
│   ├── Models/                   # State machine
│   ├── Views/                    # RecordButton
│   ├── ContentView.swift
│   └── AIVoiceWatchApp.swift     # Entry point
├── Shared/                       # Shared types (both targets)
│   └── AudioMessage.swift
├── project.yml                   # XcodeGen project spec
└── README.md
```

## License

Private. All rights reserved.
