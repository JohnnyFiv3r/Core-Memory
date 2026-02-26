FROM node:22-slim
RUN corepack enable && corepack prepare pnpm@10.0.0 --activate
WORKDIR /app

# Install deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/shared-types/package.json packages/shared-types/
COPY apps/voice-server/package.json apps/voice-server/
RUN pnpm install --frozen-lockfile

# Copy source + context
COPY packages/shared-types/ packages/shared-types/
COPY apps/voice-server/ apps/voice-server/
COPY tsconfig.base.json ./

EXPOSE 8787
CMD ["npx", "tsx", "apps/voice-server/src/index.ts"]
