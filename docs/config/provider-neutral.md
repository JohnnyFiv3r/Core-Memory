# Provider-neutral configuration

Core Memory is local-first and provider-neutral. Configure chat and embeddings
with the same shape regardless of whether the endpoint is local or hosted.

## Common shape

```bash
CORE_MEMORY_CHAT_PROVIDER=openai-compatible|openai|openrouter|ollama|lmstudio|vllm|anthropic|google
CORE_MEMORY_CHAT_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_CHAT_API_KEY=local-or-hosted-key
CORE_MEMORY_CHAT_MODEL=llama3.1

CORE_MEMORY_EMBEDDINGS_PROVIDER=openai-compatible|openai|openrouter|ollama|lmstudio|vllm|google|hash
CORE_MEMORY_EMBEDDINGS_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_EMBEDDINGS_API_KEY=local-or-hosted-key
CORE_MEMORY_EMBEDDINGS_MODEL=nomic-embed-text
```

`CORE_MEMORY_LLM_*` and singular `CORE_MEMORY_EMBEDDING_*` aliases are also accepted.

## Local-only / no hosted provider

```bash
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed
CORE_MEMORY_EMBEDDINGS_PROVIDER=hash
```

## Ollama / LM Studio / vLLM / OpenAI-compatible

```bash
CORE_MEMORY_CHAT_PROVIDER=openai-compatible
CORE_MEMORY_CHAT_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_CHAT_API_KEY=local
CORE_MEMORY_CHAT_MODEL=llama3.1
CORE_MEMORY_EMBEDDINGS_PROVIDER=openai-compatible
CORE_MEMORY_EMBEDDINGS_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_EMBEDDINGS_API_KEY=local
CORE_MEMORY_EMBEDDINGS_MODEL=nomic-embed-text
```

## OpenRouter

```bash
CORE_MEMORY_CHAT_PROVIDER=openrouter
CORE_MEMORY_CHAT_API_KEY=$OPENROUTER_API_KEY
CORE_MEMORY_CHAT_MODEL=openai/gpt-4o-mini
```

## OpenAI

```bash
CORE_MEMORY_CHAT_PROVIDER=openai
OPENAI_API_KEY=...
CORE_MEMORY_CHAT_MODEL=gpt-4o-mini
CORE_MEMORY_EMBEDDINGS_PROVIDER=openai
CORE_MEMORY_EMBEDDINGS_MODEL=text-embedding-3-small
```

## Anthropic

```bash
CORE_MEMORY_CHAT_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
CORE_MEMORY_CHAT_MODEL=claude-haiku-4-5-20251001
```

## Google / Gemini

```bash
CORE_MEMORY_CHAT_PROVIDER=google
GOOGLE_API_KEY=...
CORE_MEMORY_CHAT_MODEL=gemini-1.5-flash
CORE_MEMORY_EMBEDDINGS_PROVIDER=google
CORE_MEMORY_EMBEDDINGS_MODEL=gemini-embedding-001
```
