#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${1:-john-voice-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# From apps/web/scripts -> repo root is 3 levels up (portfolio-voice-agent)
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Load .env so CLOUDFLARE_API_TOKEN (and Cursor_Key) are available for the bot
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

# Clean dist (avoids root-owned files from Docker builds)
rm -rf apps/web/dist

# Ensure deps are installed (safe if already up to date)
pnpm install

# Portfolio voice backend defaults (override by exporting these before running script)
export VITE_VOICE_SERVER_WS_URL="${VITE_VOICE_SERVER_WS_URL:-wss://portfolio-api.wristchat.net}"
export VITE_VOICE_SERVER_HTTP_URL="${VITE_VOICE_SERVER_HTTP_URL:-https://portfolio-api.wristchat.net}"

pnpm --filter @portfolio/web build
npx wrangler pages project create "$PROJECT_NAME" --production-branch main 2>/dev/null || true
npx wrangler pages deploy apps/web/dist --project-name "$PROJECT_NAME"
