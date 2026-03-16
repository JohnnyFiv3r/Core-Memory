from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .io_utils import atomic_write_json


def _archive_paths(root: Path) -> tuple[Path, Path]:
    beads_dir = root / ".beads"
    return beads_dir / "archive.jsonl", beads_dir / "archive_index.json"


def _read_index(index_file: Path) -> dict:
    if not index_file.exists():
        return {}
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def append_archive_snapshot(root: Path, row: dict) -> dict:
    """Append one archive snapshot row and update archive_index.json.

    Returns metadata: {revision_id, offset, length, bead_id, archived_at}
    """
    archive_file, index_file = _archive_paths(root)
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    with open(archive_file, "ab") as f:
        offset = f.tell()
        f.write(payload)
        f.flush()

    revision_id = str(row.get("revision_id") or "")
    if revision_id:
        idx = _read_index(index_file)
        idx[revision_id] = {
            "bead_id": row.get("bead_id"),
            "offset": int(offset),
            "length": int(len(payload)),
            "archived_at": row.get("archived_at"),
        }
        atomic_write_json(index_file, idx)

    return {
        "revision_id": revision_id,
        "offset": int(offset),
        "length": int(len(payload)),
        "bead_id": row.get("bead_id"),
        "archived_at": row.get("archived_at"),
    }


def read_snapshot(root: Path, revision_id: str) -> Optional[dict]:
    """O(1) archive hydration by revision_id using archive_index.json."""
    if not revision_id:
        return None
    archive_file, index_file = _archive_paths(root)
    idx = _read_index(index_file)
    meta = idx.get(revision_id)
    if not meta:
        return None

    try:
        offset = int(meta.get("offset"))
        length = int(meta.get("length"))
    except (TypeError, ValueError):
        return None

    if not archive_file.exists():
        return None

    with open(archive_file, "rb") as f:
        f.seek(offset)
        blob = f.read(length)

    if not blob:
        return None

    try:
        return json.loads(blob.decode("utf-8").strip())
    except json.JSONDecodeError:
        return None


def rebuild_archive_index(root: Path) -> dict:
    """Rebuild archive_index.json from archive.jsonl."""
    archive_file, index_file = _archive_paths(root)
    rebuilt: dict = {}

    if archive_file.exists():
        with open(archive_file, "rb") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                s = line.decode("utf-8", errors="ignore").strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except json.JSONDecodeError:
                    continue
                rev = str(row.get("revision_id") or "")
                if not rev:
                    continue
                rebuilt[rev] = {
                    "bead_id": row.get("bead_id"),
                    "offset": int(offset),
                    "length": int(len(line)),
                    "archived_at": row.get("archived_at"),
                }

    atomic_write_json(index_file, rebuilt)
    return {"ok": True, "entries": len(rebuilt), "index": str(index_file)}
