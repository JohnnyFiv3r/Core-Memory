from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_faiss_warning_emitted = False


def _paths(root: Path) -> tuple[Path, Path, Path]:
    beads_dir = root / ".beads"
    return beads_dir / "index.json", beads_dir / "bead_index_meta.json", beads_dir / "bead_index.faiss"


def _tokenize(text: str) -> set[str]:
    return {t for t in (text or "").lower().replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def _embed_text(bead: dict) -> str:
    summary = " ".join(bead.get("summary") or [])
    tags = " ".join(bead.get("tags") or [])
    return f"{bead.get('type','')} | {bead.get('title','')} | {summary} | tags:{tags}".strip()


def _hash_vectors(texts: list[str], dim: int = 256):
    import numpy as np  # type: ignore

    vecs = np.zeros((len(texts), dim), dtype="float32")
    for i, t in enumerate(texts):
        for tok in _tokenize(t):
            h = abs(hash(tok)) % dim
            vecs[i, h] += 1.0
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def _provider_vectors(texts: list[str], provider: str, model: str):
    import numpy as np  # type: ignore

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("missing_openai_api_key")
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=key)
        rows = []
        for t in texts:
            emb = client.embeddings.create(model=model, input=t)
            rows.append(emb.data[0].embedding)
        vecs = np.array(rows, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("missing_gemini_api_key")

        import json as _json
        import urllib.request as _urlreq

        rows = []
        for t in texts:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={key}"
            payload = {
                "content": {
                    "parts": [{"text": t}]
                }
            }
            data = _json.dumps(payload).encode("utf-8")
            req = _urlreq.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with _urlreq.urlopen(req, timeout=30) as resp:  # nosec - trusted provider endpoint
                body = _json.loads(resp.read().decode("utf-8"))
            vals = (((body or {}).get("embedding") or {}).get("values") or [])
            if not vals:
                raise RuntimeError("gemini_embedding_empty")
            rows.append(vals)

        vecs = np.array(rows, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    raise RuntimeError(f"unsupported_provider:{provider}")


def build_semantic_index(root: Path) -> dict:
    """Build semantic lookup artifacts.

    Modes:
    - provider embeddings (default): CORE_MEMORY_EMBEDDINGS_PROVIDER=gemini
    - deterministic local hash embeddings (explicit only): CORE_MEMORY_EMBEDDINGS_PROVIDER=hash
    - lexical fallback when faiss/provider unavailable
    """
    index_file, meta_file, faiss_file = _paths(root)
    if not index_file.exists():
        return {"ok": False, "error": "index_missing"}

    index = json.loads(index_file.read_text(encoding="utf-8"))
    beads = [
        b for b in list((index.get("beads") or {}).values())
        if str((b or {}).get("status") or "").lower() != "superseded"
    ]

    rows: list[dict[str, Any]] = []
    texts: list[str] = []
    for b in beads:
        txt = _embed_text(b)
        rows.append(
            {
                "bead_id": b.get("id"),
                "type": b.get("type"),
                "status": b.get("status"),
                "session_id": b.get("session_id"),
                "created_at": b.get("created_at"),
                "text": txt,
                "tokens": sorted(_tokenize(txt)),
            }
        )
        texts.append(txt)

    backend = "lexical"
    provider = (os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "gemini").strip().lower()
    model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or "text-embedding-004").strip()

    try:
        import faiss  # type: ignore

        if provider == "hash":
            vecs = _hash_vectors(texts, dim=256)
            backend = "faiss-hash"
        else:
            try:
                vecs = _provider_vectors(texts, provider=provider, model=model)
                backend = f"faiss-{provider}"
            except Exception as exc:
                logger.warning(
                    "core-memory: embedding provider '%s' unavailable (%s). "
                    "Falling back to lexical search. To fix: set CORE_MEMORY_EMBEDDINGS_PROVIDER=hash "
                    "or install the provider SDK (pip install core-memory[openai]).",
                    provider, exc,
                )
                backend = "lexical"
                raise

        dim = int(vecs.shape[1]) if len(texts) else 256
        idx = faiss.IndexFlatIP(dim)
        if len(texts):
            idx.add(vecs)
        faiss.write_index(idx, str(faiss_file))
    except ImportError:
        global _faiss_warning_emitted
        if not _faiss_warning_emitted:
            logger.warning(
                "core-memory: faiss-cpu and/or numpy not installed. Semantic search will use lexical fallback. "
                "Install with: pip install core-memory[semantic]"
            )
            _faiss_warning_emitted = True
        backend = "lexical"
    except Exception:
        backend = "lexical"

    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(
        json.dumps({"backend": backend, "provider": provider or "gemini", "model": model, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {"ok": True, "backend": backend, "entries": len(rows), "meta": str(meta_file), "faiss": str(faiss_file)}


def semantic_lookup(root: Path, query: str, k: int = 8) -> dict:
    _, meta_file, faiss_file = _paths(root)
    if not meta_file.exists():
        b = build_semantic_index(root)
        if not b.get("ok"):
            return b

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    rows = meta.get("rows") or []
    backend = str(meta.get("backend") or "lexical")
    provider = str(meta.get("provider") or "gemini")
    model = str(meta.get("model") or "text-embedding-3-small")

    q_tokens = _tokenize(query)

    def lexical_rank() -> list[dict]:
        scored = []
        for r in rows:
            tks = set(r.get("tokens") or [])
            ov = len(q_tokens.intersection(tks)) if q_tokens else 0
            scored.append({"bead_id": r.get("bead_id"), "score": float(ov), "type": r.get("type"), "status": r.get("status")})
        scored = sorted(scored, key=lambda x: (x.get("score", 0.0), str(x.get("bead_id") or "")), reverse=True)
        return scored[: max(1, int(k))]

    if backend.startswith("faiss") and faiss_file.exists():
        try:
            import numpy as np  # type: ignore
            import faiss  # type: ignore

            idx = faiss.read_index(str(faiss_file))
            dim = idx.d
            if backend == "faiss-hash":
                q = np.zeros((1, dim), dtype="float32")
                for tok in q_tokens:
                    h = abs(hash(tok)) % dim
                    q[0, h] += 1.0
                n = np.linalg.norm(q, axis=1, keepdims=True)
                n[n == 0] = 1.0
                q = q / n
            elif backend == "faiss-openai":
                q = _provider_vectors([query], provider="openai", model=model)
            else:
                q = _hash_vectors([query], dim=dim)

            D, I = idx.search(q, max(1, int(k)))
            out = []
            for dist, row_idx in zip(D[0].tolist(), I[0].tolist()):
                if row_idx < 0 or row_idx >= len(rows):
                    continue
                r = rows[row_idx]
                out.append({"bead_id": r.get("bead_id"), "score": float(dist), "type": r.get("type"), "status": r.get("status")})
            return {"ok": True, "backend": backend, "provider": provider, "query": query, "results": out}
        except ImportError:
            global _faiss_warning_emitted
            if not _faiss_warning_emitted:
                logger.warning(
                    "core-memory: faiss-cpu/numpy not available for semantic lookup. "
                    "Install with: pip install core-memory[semantic]"
                )
                _faiss_warning_emitted = True
        except Exception as exc:
            logger.debug("core-memory: semantic lookup failed, using lexical fallback: %s", exc)

    return {"ok": True, "backend": "lexical", "provider": provider, "query": query, "results": lexical_rank()}
