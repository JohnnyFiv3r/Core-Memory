#!/bin/bash
# Start Piper TTS server with auto-restart
PIPER_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="/tmp/piper-tts.pid"
LOG_FILE="/tmp/piper-tts.log"

# Check if already running and healthy
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        # Process exists — check if it's responsive
        HEALTH=$(curl -sS --max-time 2 http://localhost:18790/health 2>/dev/null)
        if echo "$HEALTH" | grep -q '"ok":true'; then
            echo "Piper TTS healthy (PID $PID)"
            exit 0
        fi
        # Process exists but not healthy — kill and restart
        echo "Piper TTS unresponsive, restarting..."
        kill "$PID" 2>/dev/null
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

# Start server
cd "$PIPER_DIR"
LD_LIBRARY_PATH="$PIPER_DIR" \
OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-kSxqdOXuY4xQypPYFqWa_TFgGRj72Cun6YGl09UaQNw}" \
nohup node tts-server.js >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
sleep 1

# Verify it started
HEALTH=$(curl -sS --max-time 3 http://localhost:18790/health 2>/dev/null)
if echo "$HEALTH" | grep -q '"ok":true'; then
    echo "Piper TTS started (PID $!)"
else
    echo "Piper TTS may have failed to start — check $LOG_FILE"
fi
