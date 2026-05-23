# Local-first Core Memory quickstart

Core Memory should not require a hosted model account. Start with MCP and a
local/lexical store, then opt into any provider adapter you want.

## 1. No hosted provider: install MCP without hosted providers

```bash
python -m pip install 'core-memory[mcp]'
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed core-memory mcp version
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed core-memory mcp serve --host 127.0.0.1 --port 8000
```

## 2. MCP client config

```json
{
  "mcpServers": {
    "core-memory": {
      "url": "http://127.0.0.1:8000/mcp/"
    }
  }
}
```

## 3. Local OpenAI-compatible providers

Ollama:

```bash
CORE_MEMORY_CHAT_PROVIDER=ollama
CORE_MEMORY_CHAT_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_CHAT_API_KEY=local
CORE_MEMORY_CHAT_MODEL=llama3.1
CORE_MEMORY_EMBEDDINGS_PROVIDER=ollama
CORE_MEMORY_EMBEDDINGS_BASE_URL=http://localhost:11434/v1
CORE_MEMORY_EMBEDDINGS_API_KEY=local
CORE_MEMORY_EMBEDDINGS_MODEL=nomic-embed-text
```

LM Studio:

```bash
CORE_MEMORY_CHAT_PROVIDER=lmstudio
CORE_MEMORY_CHAT_BASE_URL=http://localhost:1234/v1
CORE_MEMORY_CHAT_API_KEY=local
```

vLLM:

```bash
CORE_MEMORY_CHAT_PROVIDER=vllm
CORE_MEMORY_CHAT_BASE_URL=http://localhost:8000/v1
CORE_MEMORY_CHAT_API_KEY=local
```

## 4. OpenRouter

```bash
python -m pip install 'core-memory[mcp]'
CORE_MEMORY_CHAT_PROVIDER=openrouter
CORE_MEMORY_CHAT_API_KEY=$OPENROUTER_API_KEY
CORE_MEMORY_CHAT_MODEL=openai/gpt-4o-mini
```

## 5. Hosted adapters when explicitly selected

```bash
python -m pip install 'core-memory[openai]'
python -m pip install 'core-memory[anthropic]'
python -m pip install 'core-memory[google]'
```

## Troubleshooting

- `missing_chat_provider`: set `CORE_MEMORY_CHAT_PROVIDER` or use local/lexical mode.
- `missing_openai_compatible_base_url`: set `CORE_MEMORY_CHAT_BASE_URL` / `CORE_MEMORY_EMBEDDINGS_BASE_URL` for local servers.
- `missing_*_api_key`: either set the matching key or use a localhost OpenAI-compatible endpoint with `CORE_MEMORY_*_API_KEY=local`.
- `MCP HTTP server requires core-memory[mcp]`: reinstall with `python -m pip install 'core-memory[mcp]'`.

## PyPI verification

From a checkout, run:

```bash
python scripts/verify_pypi_mcp.py
```

The script builds a wheel, installs `core-memory[mcp]` into a clean venv, runs
`core-memory mcp version`, starts `core-memory mcp serve`, initializes an MCP
client, and lists tools.
