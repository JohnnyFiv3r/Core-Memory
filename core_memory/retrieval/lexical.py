from __future__ import annotations

import json
import logging
import math
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FIELD_WEIGHTS = {
    "title": 2.6,
    "tags": 3.0,
    "incident": 3.0,
    "summary": 1.2,
    "type": 1.0,
}

_CACHE_FILE = "lexical_cache.json"


def _tokenize(text: str) -> list[str]:
    return [t for t in (text or "").lower().replace("_", " ").replace("-", " ").split() if len(t) >= 3]


def _field_tokens(bead: dict) -> dict[str, list[str]]:
    return {
        "type": _tokenize(str(bead.get("type") or "")),
        "title": _tokenize(str(bead.get("title") or "")),
        "summary": _tokenize(" ".join(bead.get("summary") or [])),
        "tags": _tokenize(" ".join(bead.get("tags") or [])),
        "incident": _tokenize(str(bead.get("incident_id") or "")),
    }


class LexicalIndex:
    """Cached TF-IDF lexical index.

    Builds once from the bead corpus, persists to .beads/lexical_cache.json,
    and supports incremental updates when beads are added. Invalidated on
    rebuild_index().
    """

    def __init__(self, root: Path):
        self._root = root
        self._cache_path = root / ".beads" / _CACHE_FILE
        self._docs: list[dict[str, Any]] | None = None
        self._df: Counter | None = None
        self._bead_ids: set[str] | None = None

    def _load_cache(self) -> bool:
        """Load cached index from disk. Returns True if loaded successfully."""
        if not self._cache_path.exists():
            return False
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            self._docs = data.get("docs") or []
            self._df = Counter(data.get("df") or {})
            self._bead_ids = set(d["bead_id"] for d in self._docs)
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        """Persist cache to disk."""
        if self._docs is None or self._df is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "docs": self._docs,
                "df": dict(self._df),
            }
            self._cache_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("core-memory: failed to save lexical cache: %s", exc)

    def _build_from_beads(self, beads: list[dict]) -> None:
        """Build the full index from a list of beads."""
        self._docs = []
        self._df = Counter()
        self._bead_ids = set()

        for b in beads:
            bead_id = str(b.get("id") or "")
            if not bead_id:
                continue
            f = _field_tokens(b)
            merged: list[str] = []
            for fk, toks in f.items():
                w = int(round(FIELD_WEIGHTS.get(fk, 1.0) * 10))
                merged.extend(toks * max(1, w))

            doc = {
                "bead_id": bead_id,
                "type": str(b.get("type") or ""),
                "status": str(b.get("status") or ""),
                "tokens": merged,
            }
            self._docs.append(doc)
            self._bead_ids.add(bead_id)
            for t in set(merged):
                self._df[t] += 1

        self._save_cache()

    def build(self, beads: list[dict] | None = None) -> None:
        """Build or rebuild the index.

        If beads is None, loads from index.json.
        """
        if beads is None:
            index_file = self._root / ".beads" / "index.json"
            if not index_file.exists():
                self._docs = []
                self._df = Counter()
                self._bead_ids = set()
                return
            idx = json.loads(index_file.read_text(encoding="utf-8"))
            beads = [
                b for b in list((idx.get("beads") or {}).values())
                if str((b or {}).get("status") or "").lower() != "superseded"
            ]
        self._build_from_beads(beads)

    def add_bead(self, bead: dict) -> None:
        """Incrementally add a single bead to the index."""
        if self._docs is None:
            self.ensure_loaded()

        bead_id = str(bead.get("id") or "")
        if not bead_id or (self._bead_ids and bead_id in self._bead_ids):
            return

        f = _field_tokens(bead)
        merged: list[str] = []
        for fk, toks in f.items():
            w = int(round(FIELD_WEIGHTS.get(fk, 1.0) * 10))
            merged.extend(toks * max(1, w))

        doc = {
            "bead_id": bead_id,
            "type": str(bead.get("type") or ""),
            "status": str(bead.get("status") or ""),
            "tokens": merged,
        }
        self._docs.append(doc)  # type: ignore[union-attr]
        self._bead_ids.add(bead_id)  # type: ignore[union-attr]
        for t in set(merged):
            self._df[t] += 1  # type: ignore[index]

        self._save_cache()

    def invalidate(self) -> None:
        """Invalidate the cached index (e.g., on rebuild_index)."""
        self._docs = None
        self._df = None
        self._bead_ids = None
        if self._cache_path.exists():
            try:
                self._cache_path.unlink()
            except Exception:
                pass

    def ensure_loaded(self) -> None:
        """Ensure the index is loaded (from cache or built fresh)."""
        if self._docs is not None:
            return
        if not self._load_cache():
            self.build()

    def lookup(self, query: str, k: int = 8) -> dict:
        """Query the index. Returns results compatible with the old lexical_lookup()."""
        self.ensure_loaded()

        q_tokens = _tokenize(query)
        if not q_tokens or not self._docs:
            return {"ok": True, "backend": "lexical-field-tfidf-cached", "query": query, "results": []}

        N = max(1, len(self._docs))
        scored = []
        for doc in self._docs:
            bead_id = doc["bead_id"]
            if not bead_id:
                continue
            tf = Counter(doc["tokens"])
            score = 0.0
            for qt in q_tokens:
                if tf.get(qt, 0) <= 0:
                    continue
                idf = math.log((1 + N) / (1 + self._df.get(qt, 0))) + 1.0  # type: ignore[union-attr]
                score += (1.0 + math.log(tf[qt])) * idf
            if score > 0:
                scored.append({"bead_id": bead_id, "score": float(score), "type": doc["type"], "status": doc["status"]})

        scored = sorted(scored, key=lambda x: (x.get("score", 0.0), x.get("bead_id", "")), reverse=True)
        return {"ok": True, "backend": "lexical-field-tfidf-cached", "query": query, "results": scored[: max(1, int(k))]}


# Module-level cache: one LexicalIndex per root
_index_cache: dict[str, LexicalIndex] = {}


def get_lexical_index(root: Path) -> LexicalIndex:
    """Get or create a cached LexicalIndex for the given root."""
    key = str(root)
    if key not in _index_cache:
        _index_cache[key] = LexicalIndex(root)
    return _index_cache[key]


def invalidate_lexical_cache(root: Path) -> None:
    """Invalidate the lexical cache for the given root."""
    key = str(root)
    if key in _index_cache:
        _index_cache[key].invalidate()
        del _index_cache[key]


def lexical_lookup(root: Path, query: str, k: int = 8) -> dict:
    """Backward-compatible entry point. Now uses LexicalIndex with caching."""
    index = get_lexical_index(root)
    return index.lookup(query, k=k)
