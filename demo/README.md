# Core Memory Demo

This demo is an observability + benchmark studio for Core Memory.

## What it shows

- **Chat** with memory-backed responses
- **Memory** (beads, associations, rolling-window records)
- **Claims** (resolved slot state, conflicts, status)
- **Runtime** (queue health, semantic backend, last flush, myelination snapshot)
- **Benchmark** (isolated LOCOMO-like runs with per-bucket + failing-case drilldown)

## Dependencies

Install from repo root:

```bash
pip install -e ".[pydanticai]"
pip install fastapi uvicorn python-dotenv
```

Optional semantic extras:

```bash
pip install -e ".[semantic]"
```

## Environment

Create/update `.env` at repo root with one provider key:

```bash
OPENAI_API_KEY=...
# or
ANTHROPIC_API_KEY=...
```

For semantic embeddings (Gemini path), also set:

```bash
GEMINI_API_KEY=...
CORE_MEMORY_EMBEDDINGS_PROVIDER=gemini
CORE_MEMORY_EMBEDDINGS_MODEL=gemini-embedding-001
```

## Run

```bash
python demo/app.py --host 127.0.0.1 --port 8080
```

Then open `http://127.0.0.1:8080`.

Presentation script:

- `demo/DEMO_SCRIPT.md`

## Benchmark isolation modes

From the Benchmark controls:

- `root=snapshot` (default): copies current demo store into isolated temp benchmark root
- `root=clean`: benchmark from fresh isolated temp root

Benchmark runs never mutate `demo/memory_store` directly.

## Troubleshooting

- Missing `pydantic_ai`:
  - `pip install -e ".[pydanticai]"`
- Missing FastAPI/Uvicorn:
  - `pip install fastapi uvicorn`
- Missing dotenv:
  - `pip install python-dotenv`
- Semantic degraded warnings:
  - install semantic extras (`.[semantic]`) and configure provider key/model
