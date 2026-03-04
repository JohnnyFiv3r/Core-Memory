from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _paths(root: Path) -> tuple[Path, Path, Path]:
    beads_dir = root / ".beads"
    return beads_dir / "index.json", beads_dir / "bead_index_meta.json", beads_dir / "bead_index.faiss"


def _tokenize(text: str) -> set[str]:
    return {t for t in (text or "").lower().replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def _embed_text(bead: dict) -> str:
    summary = " ".join(bead.get("summary") or [])
    tags = " ".join(bead.get("tags") or [])
    return f"{bead.get('type','')} | {bead.get('title','')} | {summary} | tags:{tags}".strip()


def build_semantic_index(root: Path) -> dict:
    """Build semantic lookup artifacts.

    If faiss/numpy are available, writes bead_index.faiss + meta.
    Otherwise writes meta with tokenized text for deterministic lexical fallback.
    """
    index_file, meta_file, faiss_file = _paths(root)
    if not index_file.exists():
        return {"ok": False, "error": "index_missing"}

    index = json.loads(index_file.read_text(encoding="utf-8"))
    beads = list((index.get("beads") or {}).values())

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
    try:
        import numpy as np  # type: ignore
        import faiss  # type: ignore

        # Deterministic tiny embedding via hashed bag (no network dependencies).
        dim = 256
        vecs = np.zeros((len(texts), dim), dtype="float32")
        for i, t in enumerate(texts):
            for tok in _tokenize(t):
                h = abs(hash(tok)) % dim
                vecs[i, h] += 1.0
        # L2 normalize
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms

        idx = faiss.IndexFlatIP(dim)
        idx.add(vecs)
        faiss.write_index(idx, str(faiss_file))
        backend = "faiss"
    except Exception:
        backend = "lexical"

    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"backend": backend, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "backend": backend, "entries": len(rows), "meta": str(meta_file), "faiss": str(faiss_file)}


def semantic_lookup(root: Path, query: str, k: int = 8) -> dict:
    index_file, meta_file, faiss_file = _paths(root)
    if not meta_file.exists():
        b = build_semantic_index(root)
        if not b.get("ok"):
            return b

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    rows = meta.get("rows") or []
    backend = str(meta.get("backend") or "lexical")

    q_tokens = _tokenize(query)

    # lexical fallback (also used for deterministic tie-breaking when faiss unavailable)
    def lexical_rank() -> list[dict]:
        scored = []
        for r in rows:
            tks = set(r.get("tokens") or [])
            ov = len(q_tokens.intersection(tks)) if q_tokens else 0
            scored.append({"bead_id": r.get("bead_id"), "score": float(ov), "type": r.get("type"), "status": r.get("status")})
        scored = sorted(scored, key=lambda x: (x.get("score", 0.0), str(x.get("bead_id") or "")), reverse=True)
        return scored[: max(1, int(k))]

    if backend == "faiss" and faiss_file.exists():
        try:
            import numpy as np  # type: ignore
            import faiss  # type: ignore

            dim = faiss.read_index(str(faiss_file)).d
            q = np.zeros((1, dim), dtype="float32")
            for tok in q_tokens:
                h = abs(hash(tok)) % dim
                q[0, h] += 1.0
            n = np.linalg.norm(q, axis=1, keepdims=True)
            n[n == 0] = 1.0
            q = q / n

            idx = faiss.read_index(str(faiss_file))
            D, I = idx.search(q, max(1, int(k)))
            out = []
            for dist, row_idx in zip(D[0].tolist(), I[0].tolist()):
                if row_idx < 0 or row_idx >= len(rows):
                    continue
                r = rows[row_idx]
                out.append({"bead_id": r.get("bead_id"), "score": float(dist), "type": r.get("type"), "status": r.get("status")})
            return {"ok": True, "backend": "faiss", "query": query, "results": out}
        except Exception:
            pass

    return {"ok": True, "backend": "lexical", "query": query, "results": lexical_rank()}
