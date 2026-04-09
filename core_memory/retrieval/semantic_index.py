from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .vector_backend import create_vector_backend
from .visible_corpus import build_visible_corpus
from .lifecycle import enqueue_semantic_rebuild
from .normalize import tokenize

logger = logging.getLogger(__name__)
_faiss_warning_emitted = False

SEMANTIC_MODE_REQUIRED = "required"
SEMANTIC_MODE_DEGRADED_ALLOWED = "degraded_allowed"

VECTOR_BACKEND_LOCAL_FAISS = "local-faiss"
VECTOR_BACKEND_QDRANT = "qdrant"
VECTOR_BACKEND_PGVECTOR = "pgvector"
VECTOR_BACKEND_CHROMADB = "chromadb"
_EXTERNAL_VECTOR_BACKENDS = {VECTOR_BACKEND_QDRANT, VECTOR_BACKEND_PGVECTOR, VECTOR_BACKEND_CHROMADB}


def _normalize_vector_backend(value: str | None) -> str:
    v = str(value or VECTOR_BACKEND_LOCAL_FAISS).strip().lower().replace("_", "-")
    if v in {"", "auto", "local", "faiss", "local-faiss"}:
        return VECTOR_BACKEND_LOCAL_FAISS
    if v in {"qdrant"}:
        return VECTOR_BACKEND_QDRANT
    if v in {"pgvector", "postgres", "postgresql"}:
        return VECTOR_BACKEND_PGVECTOR
    if v in {"chromadb", "chroma"}:
        return VECTOR_BACKEND_CHROMADB
    return VECTOR_BACKEND_LOCAL_FAISS


def _configured_vector_backend() -> str:
    return _normalize_vector_backend(os.environ.get("CORE_MEMORY_VECTOR_BACKEND"))


def _vector_collection_name(root: Path) -> str:
    import hashlib as _hashlib

    prefix = str(os.environ.get("CORE_MEMORY_VECTOR_COLLECTION") or "core_memory_beads").strip() or "core_memory_beads"
    suffix = _hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{suffix}"


def _create_external_backend(*, root: Path, backend: str, dimension: int):
    collection = _vector_collection_name(root)
    if backend == VECTOR_BACKEND_QDRANT:
        return create_vector_backend(
            "qdrant",
            collection_name=collection,
            url=str(os.environ.get("CORE_MEMORY_QDRANT_URL") or "http://localhost:6333"),
            dimensions=int(max(1, dimension)),
        )
    if backend == VECTOR_BACKEND_PGVECTOR:
        table_name = str(os.environ.get("CORE_MEMORY_PGVECTOR_TABLE") or collection).replace("-", "_")
        return create_vector_backend(
            "pgvector",
            table_name=table_name,
            dimensions=int(max(1, dimension)),
            dsn=os.environ.get("CORE_MEMORY_PG_DSN"),
        )
    if backend == VECTOR_BACKEND_CHROMADB:
        persist_dir = str(os.environ.get("CORE_MEMORY_CHROMADB_PERSIST_DIR") or (root / ".beads" / "semantic" / "chromadb"))
        return create_vector_backend(
            "chromadb",
            collection_name=collection,
            persist_directory=persist_dir,
        )
    raise ValueError(f"unsupported_external_backend:{backend}")


def _backend_deployment_profile(backend: str) -> dict[str, Any]:
    b = str(backend or "").strip().lower()
    if b.startswith("faiss"):
        return {
            "deployment_profile": "single_process_single_writer",
            "multi_worker_safe": False,
            "concurrency_warning": "FAISS/local index is development-oriented and not recommended for concurrent multi-worker writes.",
        }
    if b.startswith("chromadb") or b.startswith("chroma"):
        return {
            "deployment_profile": "single_process_single_writer",
            "multi_worker_safe": False,
            "concurrency_warning": "ChromaDB local persistence is best treated as single-writer unless externally coordinated.",
        }
    if b.startswith("qdrant") or b.startswith("pgvector") or b.startswith("postgres"):
        return {
            "deployment_profile": "distributed_safe",
            "multi_worker_safe": True,
            "concurrency_warning": "",
        }
    if b in {"", "lexical", "not_built"}:
        return {
            "deployment_profile": "lexical_only",
            "multi_worker_safe": True,
            "concurrency_warning": "No semantic backend is currently active; query-based anchors may fail closed in required mode.",
        }
    return {
        "deployment_profile": "unknown",
        "multi_worker_safe": False,
        "concurrency_warning": "Unknown backend profile; verify multi-worker safety before production use.",
    }


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
    rows_count = len(_read_rows(rows_file)) if rows_file.exists() else 0

    normalized_backend = _normalize_vector_backend(backend)
    ext_backend = normalized_backend in _EXTERNAL_VECTOR_BACKENDS
    connectivity_ok = True
    connectivity_error = ""
    if ext_backend:
        connectivity_ok, connectivity_error = _external_backend_connectivity(root=root, backend=normalized_backend, manifest=manifest)

    usable_backend = bool(
        (backend.startswith("faiss") and faiss_file.exists() and rows_count > 0)
        or (ext_backend and rows_count > 0 and connectivity_ok)
    )
    profile = _backend_deployment_profile(backend or "not_built")

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
        "connectivity_checked": bool(ext_backend),
        "connectivity_ok": bool(connectivity_ok if ext_backend else True),
        "connectivity_error": str(connectivity_error or ""),
        "usable_backend": usable_backend,
        "deployment_profile": str(profile.get("deployment_profile") or "unknown"),
        "multi_worker_safe": bool(profile.get("multi_worker_safe")),
        "concurrency_warning": str(profile.get("concurrency_warning") or ""),
        "recommended_production_backends": ["qdrant", "pgvector"],
        "next_step": next_step,
    }


def _external_backend_connectivity(*, root: Path, backend: str, manifest: dict[str, Any]) -> tuple[bool, str]:
    b = _normalize_vector_backend(backend)
    if b == VECTOR_BACKEND_QDRANT:
        base = str(os.environ.get("CORE_MEMORY_QDRANT_URL") or "http://localhost:6333").strip().rstrip("/")
        if not base:
            return False, "missing_qdrant_url"
        url = f"{base}/collections"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:  # nosec - configured endpoint
                code = int(getattr(resp, "status", 200) or 200)
            return (200 <= code < 500), ""
        except Exception as exc:
            return False, f"qdrant_unreachable:{exc}"

    if b == VECTOR_BACKEND_PGVECTOR:
        dsn = str(os.environ.get("CORE_MEMORY_PG_DSN") or "").strip()
        if not dsn:
            return False, "missing_pg_dsn"
        try:
            import psycopg  # type: ignore
        except Exception as exc:
            return False, f"pgvector_driver_missing:{exc}"
        try:
            conn = psycopg.connect(dsn, connect_timeout=3)
            try:
                cur = conn.execute("SELECT 1")
                _ = cur.fetchone()
            finally:
                conn.close()
            return True, ""
        except Exception as exc:
            return False, f"pgvector_unreachable:{exc}"

    if b == VECTOR_BACKEND_CHROMADB:
        try:
            dim = int(manifest.get("dimension") or 256)
        except Exception:
            dim = 256
        try:
            backend_obj = _create_external_backend(root=root, backend=b, dimension=max(1, dim))
            _ = backend_obj.count()
            return True, ""
        except Exception as exc:
            return False, f"chromadb_unavailable:{exc}"

    return True, ""


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


def _vector_dim(vecs: Any, *, fallback: int = 0) -> int:
    try:
        if hasattr(vecs, "shape") and len(getattr(vecs, "shape")) >= 2:
            return int(vecs.shape[1])
        rows = list(vecs or [])
        if rows and isinstance(rows[0], (list, tuple)):
            return int(len(rows[0]))
    except Exception:
        pass
    return int(fallback)


def _vector_rows(vecs: Any) -> list[list[float]]:
    if hasattr(vecs, "tolist"):
        raw = vecs.tolist()
    else:
        raw = list(vecs or [])
    out: list[list[float]] = []
    for row in raw:
        if isinstance(row, (list, tuple)):
            out.append([float(x) for x in row])
    return out


def _embed_vectors(*, texts: list[str], provider: str, model: str, hash_dim: int = 256):
    p = str(provider or "").strip().lower()
    if p == "hash":
        return _hash_vectors(texts, dim=max(1, int(hash_dim)))
    return _provider_vectors(texts, provider=p, model=model)


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


def _lock_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _acquire_build_lock(path: Path, *, stale_seconds: int = 300) -> tuple[bool, dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"acquired_at": _now(), "pid": os.getpid()}
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # Try create-once lock.
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True, payload
    except FileExistsError:
        pass
    except Exception:
        return False, {"reason": "lock_create_failed"}

    # Existing lock: reclaim if stale.
    info = _lock_info(path)
    acquired_at = _parse_iso(str(info.get("acquired_at") or ""))
    is_stale = False
    if acquired_at is not None:
        age = (datetime.now(timezone.utc) - acquired_at).total_seconds()
        is_stale = age > max(5, int(stale_seconds))

    if is_stale:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            return False, {"reason": "stale_lock_unlink_failed", "lock": info}
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            payload["reclaimed_stale_lock"] = True
            return True, payload
        except Exception:
            return False, {"reason": "lock_recreate_failed", "lock": info}

    return False, {"reason": "lock_held", "lock": info}


def _release_build_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def build_semantic_index(root: Path) -> dict:
    manifest_file, faiss_file, rows_file, build_lock, _queue_file = _paths(root)

    acquired, lock_meta = _acquire_build_lock(build_lock)
    if not acquired:
        enqueue_semantic_rebuild(root)
        return {
            "ok": False,
            "retryable": True,
            "error": {
                "code": "semantic_build_lock_held",
                "message": "semantic build lock is already held",
                "detail": lock_meta,
            },
            "queued": True,
            "lock_path": str(build_lock),
        }

    try:
        corpus = build_visible_corpus(root)
        rows = _rows_from_corpus(corpus)
        texts = [str(r.get("semantic_text") or "") for r in rows]

        provider = (os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "gemini").strip().lower()
        model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or "text-embedding-004").strip()
        vector_backend = _configured_vector_backend()

        backend = "lexical"
        dim = 0
        if vector_backend in _EXTERNAL_VECTOR_BACKENDS:
            try:
                vecs = _embed_vectors(texts=texts, provider=provider, model=model, hash_dim=256)
                dim = _vector_dim(vecs, fallback=256 if texts else 0)
                vb = _create_external_backend(root=root, backend=vector_backend, dimension=dim)
                for row, emb in zip(rows, _vector_rows(vecs)):
                    vb.upsert(
                        bead_id=str(row.get("bead_id") or ""),
                        embedding=emb,
                        metadata={
                            "status": row.get("status"),
                            "session_id": row.get("session_id"),
                            "source_surface": row.get("source_surface"),
                            "created_at": row.get("created_at"),
                        },
                    )
                backend = vector_backend
                # no local faiss artifact in external backend mode
                if faiss_file.exists():
                    try:
                        faiss_file.unlink()
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(
                    "core-memory: external vector backend '%s' unavailable (%s). Falling back to lexical.",
                    vector_backend,
                    exc,
                )
                backend = "lexical"
                dim = 0
        else:
            try:
                import faiss  # type: ignore

                vecs = _embed_vectors(texts=texts, provider=provider, model=model, hash_dim=256)
                dim = _vector_dim(vecs, fallback=256 if texts else 0)
                if provider == "hash":
                    backend = "faiss-hash"
                else:
                    backend = f"faiss-{provider}"

                idx = faiss.IndexFlatIP(dim or 256)
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
                dim = 0
            except Exception as exc:
                logger.warning(
                    "core-memory: local-faiss semantic build failed (%s). Falling back to lexical.",
                    exc,
                )
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
            "vector_backend": vector_backend,
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
            "lock": {"path": str(build_lock), **dict(lock_meta or {})},
        }
    finally:
        _release_build_lock(build_lock)


def semantic_lookup(root: Path, query: str, k: int = 8, mode: str | None = None) -> dict:
    manifest_file, faiss_file, rows_file, _build_lock, queue_file = _paths(root)
    warnings: list[str] = []
    mode_n = _normalize_semantic_mode(mode)

    if not manifest_file.exists() or not rows_file.exists():
        built = build_semantic_index(root)
        if not built.get("ok"):
            code = str(((built.get("error") or {}).get("code") or "")).strip()
            if code == "semantic_build_lock_held":
                warnings.append("semantic_build_lock_held")
            else:
                warnings.append("semantic_index_build_failed")

    manifest = _read_manifest(manifest_file)
    rows = _read_rows(rows_file)

    # Provider/model mismatch -> hard rebuild before querying.
    req_provider = (os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER") or "gemini").strip().lower()
    req_model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or "text-embedding-004").strip()
    req_vector_backend = _configured_vector_backend()
    if (
        (manifest.get("provider") and (str(manifest.get("provider")) != req_provider or str(manifest.get("model")) != req_model))
        or (manifest.get("vector_backend") and _normalize_vector_backend(str(manifest.get("vector_backend"))) != req_vector_backend)
    ):
        rebuilt = build_semantic_index(root)
        if rebuilt.get("ok"):
            manifest = _read_manifest(manifest_file)
            rows = _read_rows(rows_file)
            warnings.append("semantic_index_rebuilt_config_mismatch")
        else:
            warnings.append("semantic_index_rebuild_config_mismatch_failed")

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
        if mode in {"eager", "sync"}:
            q = _read_queue(queue_file)
            if bool(q.get("queued")):
                rebuilt = build_semantic_index(root)
                if rebuilt.get("ok"):
                    warnings.append("semantic_index_rebuilt_sync")
                    manifest = _read_manifest(manifest_file)
                    rows = _read_rows(rows_file)
                    dirty = False
                else:
                    warnings.append("semantic_rebuild_sync_failed")

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
        rebuilt = build_semantic_index(root)
        if rebuilt.get("ok"):
            manifest = _read_manifest(manifest_file)
            rows = _read_rows(rows_file)
        else:
            warnings.append("semantic_index_rebuild_no_rows_failed")

    if _normalize_vector_backend(backend) in _EXTERNAL_VECTOR_BACKENDS and rows:
        try:
            backend_n = _normalize_vector_backend(backend)
            manifest_provider = str(manifest.get("provider") or req_provider)
            qv = _embed_vectors(
                texts=[query],
                provider=manifest_provider,
                model=str(manifest.get("model") or req_model),
                hash_dim=int(manifest.get("dimension") or 256),
            )
            q_rows = _vector_rows(qv)
            if not q_rows:
                raise RuntimeError("query_embedding_empty")
            vb = _create_external_backend(
                root=root,
                backend=backend_n,
                dimension=max(1, _vector_dim(qv, fallback=int(manifest.get("dimension") or 256))),
            )
            raw = vb.search(q_rows[0], k=max(1, int(k)))

            row_by_id = {str(r.get("bead_id") or ""): r for r in rows}
            out = []
            for item in raw:
                bead_id = str((item or {}).get("bead_id") or "")
                if not bead_id:
                    continue
                row = row_by_id.get(bead_id, {})
                meta = (item or {}).get("metadata") or {}
                out.append(
                    {
                        "bead_id": bead_id,
                        "score": float((item or {}).get("score") or 0.0),
                        "status": (meta.get("status") if isinstance(meta, dict) else None) or row.get("status"),
                        "anchor_reason": "retrieved",
                    }
                )

            return {
                "ok": True,
                "degraded": False,
                "backend": backend_n,
                "provider": manifest.get("provider"),
                "query": query,
                "warnings": warnings,
                "stale_age_ms": stale_age_ms,
                "results": out,
            }
        except Exception as exc:
            logger.debug("core-memory: external semantic lookup failed, using lexical fallback: %s", exc)
            warnings.append("semantic_backend_query_failed_lexical_fallback")

    if backend.startswith("faiss") and faiss_file.exists() and rows:
        try:
            import faiss  # type: ignore

            idx = faiss.read_index(str(faiss_file))
            dim = idx.d

            if backend == "faiss-hash":
                qv = _embed_vectors(texts=[query], provider="hash", model=str(manifest.get("model") or req_model), hash_dim=dim)
            elif backend == "faiss-openai":
                qv = _embed_vectors(texts=[query], provider="openai", model=str(manifest.get("model") or req_model), hash_dim=dim)
            elif backend == "faiss-gemini":
                qv = _embed_vectors(texts=[query], provider="gemini", model=str(manifest.get("model") or req_model), hash_dim=dim)
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
