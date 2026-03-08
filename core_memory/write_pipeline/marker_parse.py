from __future__ import annotations

import json
import re
from pathlib import Path

from core_memory.schema import normalize_bead_type, is_allowed_bead_type

BEAD_PATTERN = re.compile(r'<!--\s*BEAD:(.*?)-->', re.DOTALL)
ATTR_BEAD_PATTERN = re.compile(r"\{::bead\s+([^}]*)/::\}", re.IGNORECASE)
ATTR_PATTERN = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")
VALID_SCOPES = {"local", "project", "global", "identity"}
VALID_AUTHORITIES = {"agent", "user", "system"}


def _extract_assistant_text(obj: dict) -> str:
    if str(obj.get("role") or "") != "assistant":
        return ""
    content = obj.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for x in content:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                t = x.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _parse_bead_match(raw: str) -> dict | None:
    try:
        bead_data = json.loads(raw.strip())
    except Exception:
        return None
    if not isinstance(bead_data, dict) or "type" not in bead_data:
        return None

    raw_type = bead_data.get("type")
    btype = normalize_bead_type(str(raw_type))
    if not is_allowed_bead_type(btype):
        return None
    bead_data["type"] = btype

    scope = str(bead_data.get("scope") or "project").strip().lower()
    if scope not in VALID_SCOPES:
        return None
    bead_data["scope"] = scope

    authority = str(bead_data.get("authority") or "agent").strip().lower()
    if authority not in VALID_AUTHORITIES:
        return None
    bead_data["authority"] = authority

    return bead_data


def _parse_attr_bead(raw_attrs: str) -> dict | None:
    attrs = dict(ATTR_PATTERN.findall(raw_attrs or ""))
    raw_type = attrs.get("type")
    if not raw_type:
        return None
    btype = normalize_bead_type(raw_type)
    if not is_allowed_bead_type(btype):
        return None

    title = str(attrs.get("title") or "").strip()
    summary_raw = str(attrs.get("summary") or "").strip()
    summary = [x.strip() for x in summary_raw.split("|") if x.strip()] if summary_raw else []
    if not summary and title:
        summary = [title]

    scope = str(attrs.get("scope") or "project").strip().lower()
    if scope not in VALID_SCOPES:
        return None

    authority = str(attrs.get("authority") or "agent").strip().lower()
    if authority not in VALID_AUTHORITIES:
        return None

    bead = {
        "type": btype,
        "title": title or "Untitled",
        "summary": summary,
        "scope": scope,
        "authority": authority,
    }
    return bead


def extract_beads_from_transcript(path: Path) -> list[dict]:
    beads: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            txt = _extract_assistant_text(obj)
            if not txt:
                continue
            for m in BEAD_PATTERN.findall(txt):
                bead = _parse_bead_match(m)
                if bead is not None:
                    beads.append(bead)
            for m in ATTR_BEAD_PATTERN.findall(txt):
                bead = _parse_attr_bead(m)
                if bead is not None:
                    beads.append(bead)
    return beads
