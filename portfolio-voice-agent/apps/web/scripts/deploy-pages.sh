#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${1:-john-voice-agent}"

pnpm --filter web build
npx wrangler pages project create "$PROJECT_NAME" --production-branch main || true
npx wrangler pages deploy dist --project-name "$PROJECT_NAME"
