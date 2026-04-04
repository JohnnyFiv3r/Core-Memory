from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .visible_corpus import build_visible_corpus
from .lifecycle import enqueue_semantic_rebuild
from .normalize import tokenize

logger = logging.getLogger(__name__)
_faiss_warning_emitted = False

SEMANTIC_MODE_REQUIRED = "required"
SEMANTIC_MODE_DEGRADED_ALLOWED = "degraded_allowed"


def _normalize_semantic_mode(mode: str | None) -> str:
    m = str(mode or SEMANTIC_MODE_DEGRADED_ALLOWED).strip().lower()
    if m not in {SEMANTIC_MODE_REQUIRED, SEMANTIC_MODE_DEGRADED_ALLOWED}:
        return SEMANTIC_MODE_DEGRADED_ALLOWED
    return m


def semantic_doctor(root: Path) -> dict[str, Any]:
    """Operational diagnostics for canonical semantic mode."""
    manifest_file, faiss_file, rows_file, *_ = _paths(root)
    mode = _normalize_semantic_mode(os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE"))
    degraded_enabled = mode == SEMANTIC_MODE_DEGRADED_ALLOWED

    manifest: dict[str, Any] = {}
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    backend = str(manifest.get("backend") or "")
    provider = str(manifest.get("provider") or os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "")
    rows_count = 0
    if rows_file.exists():
        try:
            rows_count = len(json.loads(rows_file.read_text(encoding="utf-8")) or [])
        except Exception:
            rows_count = 0

    usable_backend = bool(backend.startswith("faiss") and faiss_file.exists() and rows_count > 0)

    if usable_backend:
        next_step = "Semantic backend is ready for canonical query-based anchor lookup."
    elif mode == SEMANTIC_MODE_REQUIRED:
        next_step = "Install semantic extras and run: core-memory graph semantic-build"
    else:
        next_step = "Degraded lexical mode is enabled; install semantic extras + run semantic-build for canonical mode."

    return {
        "ok": True,
        "mode": mode,
        "degraded_mode_enabled": degraded_enabled,
        "manifest_path": str(manifest_file),
        "manifest_exists": manifest_file.exists(),
        "backend": backend or "not_built",
        "provider": provider or "unknown",
        "rows_count": int(rows_count),
        "faiss_index_exists": faiss_file.exists(),
        "usable_backend": usable_backend,
        "next_step": next_step,
    }


def semantic_unavailable_payload(*, query: str, warnings: list[str] | None = None, provider: str | None = None) -> dict[str, Any]:
    ws = list(warnings or [])
    if "semantic_backend_unavailable" not in ws:
        ws.append("semantic_backend_unavailable")
    return {
        "ok": False,
        "degraded": False,
        "backend": "unavailable",
        "provider": provider,
        "query": query,
        "warnings": ws,
        "error": {
            "code": "semantic_backend_unavailable",
            "message": "Semantic backend unavailable for anchor lookup",
        },
        "results": [],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime | None:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    sem = root / ".beads" / "semantic"
    return (
        sem / "manifest.json",
        sem / "index.faiss",
        sem / "rows.jsonl",
        sem / "build.lock",
        sem / "rebuild-queue.json",
    )


def _read_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"queued": False, "queued_at": None, "epoch": 0}
    try:
        q = json.loads(path.read_text(encoding="utf-8"))
        return q if isinstance(q, dict) else {"queued": False, "queued_at": None, "epoch": 0}
    except Exception:
        return {"queued": False, "queued_at": None, "epoch": 0}


def _write_queue(path: Path, q: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _hash_vectors(texts: list[str], dim: int = 256):
    import numpy as np  # type: ignore

    vecs = np.zeros((len(texts), dim), dtype="float32")
    for i, t in enumerate(texts):
        for tok in tokenize(t):
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
            payload = {"content": {"parts": [{"text": t}]}}
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


def _rows_from_corpus(corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in corpus:
        txt = str(r.get("semantic_text") or "")
        rows.append(
            {
                "bead_id": str(r.get("bead_id") or ""),
                "status": str(r.get("status") or ""),
                "session_id": str(r.get("session_id") or ""),
                "source_surface": str(r.get("source_surface") or ""),
                "created_at": str(r.get("created_at") or ""),
                "semantic_text": txt,
                "semantic_text_hash": hashlib.sha256(txt.encode("utf-8")).hexdigest(),
            }
        )
    return rows


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda x: (str(x.get("bead_id") or ""), str(x.get("status") or ""))):
        h.update(str(r.get("bead_id") or "").encode("utf-8"))
        h.update(b"|")
        h.update(str(r.get("status") or "").encode("utf-8"))
        h.update(b"|")
        h.update(str(r.get("semantic_text_hash") or "").encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _write_rows(rows_file: Path, rows: list[dict[str, Any]]) -> None:
    rows_file.parent.mkdir(parents=True, exist_ok=True)
    with open(rows_file, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_rows(rows_file: Path) -> list[dict[str, Any]]:
    if not rows_file.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in rows_file.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
            if isinstance(r, dict):
                out.append(r)
        except Exception:
            continue
    return out


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_semantic_index(root: Path) -> dict:
    manifest_file, faiss_file, rows_file, _build_lock, _queue_file = _paths(root)

    corpus = build_visible_corpus(root)
    rows = _rows_from_corpus(corpus)
    texts = [str(r.get("semantic_text") or "") for r in rows]

    provider = (os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "gemini").strip().lower()
    model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or "text-embedding-004").strip()

    backend = "lexical"
    dim = 0
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
                raise

        dim = int(vecs.shape[1]) if len(texts) else 256
        idx = faiss.IndexFlatIP(dim)
        if len(texts):
            idx.add(vecs)
        faiss_file.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(idx, str(faiss_file))
    except ImportError:
        global _faiss_warning_emitted
        if not _faiss_warning_emitted:
            mode = _normalize_semantic_mode(os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE"))
            mode_hint = (
                "query-based anchor lookup may fail closed in required mode"
                if mode == SEMANTIC_MODE_REQUIRED
                else "query-based lookup may run in degraded lexical mode"
            )
            logger.warning(
                "core-memory: faiss-cpu and/or numpy not installed. %s. "
                "Install with: pip install core-memory[semantic]",
                mode_hint,
            )
            _faiss_warning_emitted = True
        backend = "lexical"
    except Exception:
        backend = "lexical"
        dim = 0

    _write_rows(rows_file, rows)
    fp = _fingerprint(rows)
    prev = _read_manifest(manifest_file)
    manifest = {
        "provider": provider,
        "model": model,
        "dimension": int(dim),
        "backend": backend,
        "backend_version": "v9-s2",
        "corpus_fingerprint": fp,
        "built_at": _now(),
        "row_count": len(rows),
        "dirty": False,
        "last_dirty_at": prev.get("last_dirty_at"),
        "last_dirty_reason": prev.get("last_dirty_reason"),
        "last_turn_id": prev.get("last_turn_id"),
        "last_flush_tx_id": prev.get("last_flush_tx_id"),
        "visible_statuses": ["open", "candidate", "promoted", "archived"],
    }
    _write_manifest(manifest_file, manifest)

    # consume queue epoch on successful build
    q = _read_queue(_queue_file)
    q["queued"] = False
    q["queued_at"] = None
    _write_queue(_queue_file, q)

    return {
        "ok": True,
        "backend": backend,
        "entries": len(rows),
        "manifest": str(manifest_file),
        "faiss": str(faiss_file),
        "rows": str(rows_file),
    }


def semantic_lookup(root: Path, query: str, k: int = 8, mode: str | None = None) -> dict:
    manifest_file, faiss_file, rows_file, build_lock, queue_file = _paths(root)
    warnings: list[str] = []
    mode_n = _normalize_semantic_mode(mode)

    if not manifest_file.exists() or not rows_file.exists():
        built = build_semantic_index(root)
        if not built.get("ok"):
            return built

    manifest = _read_manifest(manifest_file)
    rows = _read_rows(rows_file)

    # Provider/model mismatch -> hard rebuild before querying.
    req_provider = (os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "gemini").strip().lower()
    req_model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or "text-embedding-004").strip()
    if manifest.get("provider") and (
        str(manifest.get("provider")) != req_provider or str(manifest.get("model")) != req_model
    ):
        build_semantic_index(root)
        manifest = _read_manifest(manifest_file)
        rows = _read_rows(rows_file)
        warnings.append("semantic_index_rebuilt_provider_model_mismatch")

    # Dirty/fingerprint mismatch -> serve stale + enqueue rebuild when possible.
    current_fp = _fingerprint(_rows_from_corpus(build_visible_corpus(root)))
    dirty = bool(manifest.get("dirty")) or (str(manifest.get("corpus_fingerprint") or "") != current_fp)
    stale_age_ms: int | None = None
    if dirty:
        dt = _parse_iso(str(manifest.get("last_dirty_at") or ""))
        if dt is not None:
            stale_age_ms = int((datetime.now(timezone.utc) - dt).total_seconds() * 1000)
    if dirty:
        warnings.append("semantic_index_stale")
        enqueue_semantic_rebuild(root)

        # background_stale contract: never rebuild synchronously on hot query path
        # when a usable index already exists.
        mode = str(os.getenv("CORE_MEMORY_SEMANTIC_REBUILD_MODE", "background_stale") or "background_stale").strip().lower()
        if mode in {"eager", "sync"} and not build_lock.exists():
            try:
                build_lock.parent.mkdir(parents=True, exist_ok=True)
                build_lock.write_text(_now(), encoding="utf-8")
                q = _read_queue(queue_file)
                if bool(q.get("queued")):
                    build_semantic_index(root)
                    warnings.append("semantic_index_rebuilt_sync")
                    manifest = _read_manifest(manifest_file)
                    rows = _read_rows(rows_file)
                    dirty = False
            except Exception:
                warnings.append("semantic_rebuild_sync_failed")
            finally:
                if build_lock.exists():
                    try:
                        build_lock.unlink()
                    except Exception:
                        pass

    backend = str(manifest.get("backend") or "lexical")

    def lexical_rank() -> list[dict[str, Any]]:
        q_tokens = set(tokenize(query))
        scored: list[dict[str, Any]] = []
        for r in rows:
            txt = str(r.get("semantic_text") or "")
            tks = set(tokenize(txt))
            ov = len(q_tokens.intersection(tks)) if q_tokens else 0
            scored.append(
                {
                    "bead_id": r.get("bead_id"),
                    "score": float(ov),
                    "status": r.get("status"),
                    "anchor_reason": "retrieved",
                }
            )
        scored = sorted(scored, key=lambda x: (x.get("score", 0.0), str(x.get("bead_id") or "")), reverse=True)
        return scored[: max(1, int(k))]

    # If no usable rows yet, do sync build then lexical fallback response.
    if not rows:
        build_semantic_index(root)
        manifest = _read_manifest(manifest_file)
        rows = _read_rows(rows_file)

    if backend.startswith("faiss") and faiss_file.exists() and rows:
        try:
            import faiss  # type: ignore

            idx = faiss.read_index(str(faiss_file))
            dim = idx.d

            if backend == "faiss-hash":
                qv = _hash_vectors([query], dim=dim)
            elif backend == "faiss-openai":
                qv = _provider_vectors([query], provider="openai", model=str(manifest.get("model") or req_model))
            elif backend == "faiss-gemini":
                qv = _provider_vectors([query], provider="gemini", model=str(manifest.get("model") or req_model))
            else:
                raise RuntimeError(f"unsupported_backend:{backend}")

            D, I = idx.search(qv, max(1, int(k)))
            out = []
            for dist, row_idx in zip(D[0].tolist(), I[0].tolist()):
                if row_idx < 0 or row_idx >= len(rows):
                    continue
                r = rows[row_idx]
                out.append(
                    {
                        "bead_id": r.get("bead_id"),
                        "score": float(dist),
                        "status": r.get("status"),
                        "anchor_reason": "retrieved",
                    }
                )
            return {
                "ok": True,
                "degraded": False,
                "backend": backend,
                "provider": manifest.get("provider"),
                "query": query,
                "warnings": warnings,
                "stale_age_ms": stale_age_ms,
                "results": out,
            }
        except ImportError:
            global _faiss_warning_emitted
            if not _faiss_warning_emitted:
                logger.warning(
                    "core-memory: faiss-cpu/numpy not available for semantic lookup. "
                    "Install with: pip install core-memory[semantic]"
                )
                _faiss_warning_emitted = True
            warnings.append("semantic_backend_query_failed_lexical_fallback")
        except Exception as exc:
            logger.debug("core-memory: semantic lookup failed, using lexical fallback: %s", exc)
            warnings.append("semantic_backend_query_failed_lexical_fallback")

    if mode_n == SEMANTIC_MODE_REQUIRED:
        return semantic_unavailable_payload(
            query=query,
            warnings=warnings,
            provider=str(manifest.get("provider") or req_provider),
        )

    return {
        "ok": True,
        "degraded": True,
        "backend": "lexical",
        "provider": manifest.get("provider") or req_provider,
        "query": query,
        "warnings": list(warnings) + ["semantic_backend_unavailable_degraded"],
        "stale_age_ms": stale_age_ms,
        "results": lexical_rank(),
    }
