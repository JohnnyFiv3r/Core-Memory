from __future__ import annotations

import logging
import os
from pathlib import Path

from core_memory.persistence.io_utils import store_lock

_log = logging.getLogger(__name__)


class ObsidianSyncTarget:
    """Writes beads as Obsidian markdown files with YAML frontmatter and wikilinks.

    Read-side search is available when CORE_MEMORY_OBSIDIAN_REST_URL points to
    a running Obsidian Local REST API plugin. Traversal is not supported.

    Set CORE_MEMORY_OBSIDIAN_VAULT to the vault directory path.
    Set CORE_MEMORY_SYNC_TARGETS=obsidian to activate.
    """

    name = "obsidian"

    def __init__(self, vault_path: str, rest_api_url: str | None = None) -> None:
        self._vault = Path(vault_path) if vault_path else None
        self._rest_url = (rest_api_url or "").rstrip("/") or None
        if self._vault and not self._vault.exists():
            self._vault.mkdir(parents=True, exist_ok=True)
            _log.info("obsidian: created vault directory %s", self._vault)

    @classmethod
    def from_env(cls) -> "ObsidianSyncTarget":
        return cls(
            vault_path=os.environ.get("CORE_MEMORY_OBSIDIAN_VAULT", ""),
            rest_api_url=os.environ.get("CORE_MEMORY_OBSIDIAN_REST_URL"),
        )

    def _bead_path(self, bead: dict) -> Path | None:
        if not self._vault:
            return None
        session = str(bead.get("session_id") or "unsorted")
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return None
        return self._vault / session / f"{bead_id}.md"

    def _render_md(self, bead: dict) -> str:
        summary_lines = bead.get("summary") or []
        summary_md = "\n".join(f"- {line}" for line in summary_lines)
        title = str(bead.get("title") or "")
        return (
            f"---\n"
            f"id: {bead.get('id', '')}\n"
            f"type: {bead.get('type', '')}\n"
            f"title: {title}\n"
            f"status: {bead.get('status', 'open')}\n"
            f"session_id: {bead.get('session_id', '')}\n"
            f"created_at: {bead.get('created_at', '')}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"{summary_md}\n"
        )

    def on_bead_written(self, bead: dict) -> None:
        path = self._bead_path(bead)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with store_lock(path.parent):
                path.write_text(self._render_md(bead), encoding="utf-8")
        except Exception as exc:
            _log.warning("obsidian on_bead_written failed: %s", exc)

    def on_association_written(self, assoc: dict) -> None:
        if not self._vault:
            return
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if not (src and tgt):
            return
        matches = list(self._vault.glob(f"**/{src}.md"))
        if not matches:
            return
        src_path = matches[0]
        try:
            with store_lock(src_path.parent):
                existing = src_path.read_text(encoding="utf-8")
                link = f"[[{tgt}]]"
                if link not in existing:
                    src_path.write_text(
                        existing.rstrip() + f"\n\n{link}\n", encoding="utf-8"
                    )
        except Exception as exc:
            _log.warning("obsidian on_association_written failed: %s", exc)

    def on_bead_retracted(self, bead_id: str) -> None:
        if not self._vault:
            return
        matches = list(self._vault.glob(f"**/{bead_id}.md"))
        for path in matches:
            try:
                with store_lock(path.parent):
                    existing = path.read_text(encoding="utf-8")
                    updated = existing.replace("status: open", "status: retracted", 1)
                    updated = updated.replace("status: candidate", "status: retracted", 1)
                    if "RETRACTED" not in updated:
                        updated += "\n> **RETRACTED**\n"
                    path.write_text(updated, encoding="utf-8")
            except Exception as exc:
                _log.warning("obsidian on_bead_retracted failed: %s", exc)

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        bead_count = 0
        errors: list[str] = []
        for bead in beads:
            try:
                self.on_bead_written(bead)
                bead_count += 1
            except Exception as exc:
                errors.append(f"bead:{bead.get('id')}:{exc}")
        for assoc in associations:
            try:
                self.on_association_written(assoc)
            except Exception as exc:
                errors.append(f"assoc:{assoc.get('id')}:{exc}")
        return {
            "synced_beads": bead_count,
            "synced_associations": len(associations),
            "errors": errors,
        }

    def search_candidates(
        self,
        query_text: str,
        k: int = 8,
        filters: dict | None = None,
    ) -> dict:
        if not self._rest_url:
            return {"ok": False, "results": [], "warnings": ["REST API not configured"]}
        try:
            import json as _json
            import urllib.parse
            import urllib.request
            url = (
                f"{self._rest_url}/search/simple/"
                f"?query={urllib.parse.quote(query_text)}&contextLength=100"
            )
            with urllib.request.urlopen(url, timeout=5) as resp:  # nosec
                data = _json.loads(resp.read())
            results = [
                {
                    "bead_id": Path(r.get("filename", "")).stem,
                    "score": 1.0,
                    "metadata": r,
                }
                for r in (data if isinstance(data, list) else [])
            ][:k]
            return {"ok": True, "results": results, "warnings": []}
        except Exception as exc:
            return {"ok": False, "results": [], "warnings": [str(exc)]}

    def close(self) -> None:
        pass
