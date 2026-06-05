"""Multi-store recall fan-out orchestration (#15).

Fans out to configured external stores (Ragie, PipeHouse) in parallel, merges
results with Core Memory recall, and returns an augmented RecallResult.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any

from core_memory.retrieval.contracts import EvidenceItem, RecallResult

_FANOUT_TIMEOUT = 5.0


def _normalize_scores(items: list[EvidenceItem]) -> list[EvidenceItem]:
    scores = [i.score for i in items if i.score is not None]
    if not scores:
        return items
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return items
    for item in items:
        if item.score is not None:
            item.score = (item.score - lo) / (hi - lo)
    return items


def _parse_store_weights() -> dict[str, float]:
    from core_memory.config.feature_flags import external_store_weights
    raw = external_store_weights()
    defaults: dict[str, float] = {"core_memory": 1.0, "ragie": 1.0, "pipehouse": 1.0}
    if not raw:
        return defaults
    parts = [p.strip() for p in raw.split(",")]
    keys = ["core_memory", "ragie", "pipehouse"]
    out = dict(defaults)
    for i, part in enumerate(parts[:3]):
        try:
            out[keys[i]] = float(part)
        except (ValueError, IndexError):
            pass
    return out


def _apply_store_weight(items: list[EvidenceItem], weight: float) -> list[EvidenceItem]:
    if abs(weight - 1.0) < 1e-9:
        return items
    for item in items:
        if item.score is not None:
            item.score = item.score * weight
    return items


def _resolve_unifying_ids(all_items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Group items that share a core_memory_unifying_id.

    Core Memory bead is the primary; Ragie/PipeHouse items with the same
    unifying_id are deduplicated into the primary's metadata["unified_with"].
    """
    cm_by_uid: dict[str, EvidenceItem] = {}
    for item in all_items:
        if item.source_store == "core_memory" and item.unifying_id:
            cm_by_uid[item.unifying_id] = item

    if not cm_by_uid:
        return all_items

    to_remove: set[int] = set()
    for idx, item in enumerate(all_items):
        if item.source_store == "core_memory":
            continue
        if not item.unifying_id:
            continue
        primary = cm_by_uid.get(item.unifying_id)
        if primary is None:
            continue
        unified = list(primary.metadata.get("unified_with") or [])
        unified.append(item.source_ref or item.bead_id)
        primary.metadata["unified_with"] = unified
        to_remove.add(idx)

    return [item for idx, item in enumerate(all_items) if idx not in to_remove]


def fanout_recall(
    query: str,
    *,
    core_memory_result: RecallResult,
    ragie_cfg: dict[str, Any] | None,
    pipehouse_cfg: dict[str, Any] | None,
) -> RecallResult:
    """Fan out to configured external stores in parallel (ThreadPoolExecutor, timeout=5s).

    Normalizes per-store scores, applies store weights, resolves unifying IDs,
    merges and re-ranks. Populates unavailable_stores on timeout or exception.
    Returns the augmented RecallResult.
    """
    fanout_stores: list[str] = ["core_memory"]
    unavailable_stores: list[str] = []
    external_items: dict[str, list[EvidenceItem]] = {}

    tasks: dict[str, Any] = {}
    if ragie_cfg:
        fanout_stores.append("ragie")
        tasks["ragie"] = ragie_cfg
    if pipehouse_cfg:
        fanout_stores.append("pipehouse")
        tasks["pipehouse"] = pipehouse_cfg

    if not tasks:
        meta = dict(core_memory_result.metadata or {})
        meta.setdefault("fanout_stores", fanout_stores)
        meta.setdefault("unavailable_stores", [])
        core_memory_result.metadata = meta
        return core_memory_result

    def _call_ragie(cfg: dict[str, Any]) -> list[EvidenceItem]:
        from core_memory.retrieval.adapters.ragie_adapter import retrieve
        return retrieve(
            query,
            api_key=cfg["api_key"],
            top_k=int(cfg.get("top_k") or 8),
            rerank=bool(cfg.get("rerank", True)),
            partition=cfg.get("partition"),
            filter=cfg.get("filter"),
        )

    def _call_pipehouse(cfg: dict[str, Any]) -> list[EvidenceItem]:
        from core_memory.retrieval.adapters.pipehouse_adapter import retrieve
        return retrieve(
            query,
            base_url=cfg["base_url"],
            top_k=int(cfg.get("top_k") or 8),
            filters=cfg.get("filters"),
        )

    store_fn = {"ragie": _call_ragie, "pipehouse": _call_pipehouse}

    # Use explicit shutdown(wait=False) so timed-out threads don't block recall().
    # The `with` form calls shutdown(wait=True) on __exit__, defeating the timeout.
    executor = ThreadPoolExecutor(max_workers=len(tasks))
    try:
        futures = {
            store: executor.submit(store_fn[store], cfg)
            for store, cfg in tasks.items()
        }
        for store, future in futures.items():
            try:
                external_items[store] = future.result(timeout=_FANOUT_TIMEOUT)
            except FuturesTimeoutError:
                unavailable_stores.append(store)
                external_items[store] = []
            except Exception:
                unavailable_stores.append(store)
                external_items[store] = []
    finally:
        executor.shutdown(wait=False)

    weights = _parse_store_weights()

    cm_items = list(core_memory_result.evidence)
    _normalize_scores(cm_items)
    _apply_store_weight(cm_items, weights.get("core_memory", 1.0))

    all_items: list[EvidenceItem] = list(cm_items)
    for store in ("ragie", "pipehouse"):
        items = external_items.get(store) or []
        _apply_store_weight(items, weights.get(store, 1.0))
        all_items.extend(items)

    all_items = _resolve_unifying_ids(all_items)
    all_items.sort(key=lambda e: float(e.score) if e.score is not None else 0.0, reverse=True)

    result = core_memory_result
    result.evidence = all_items
    meta = dict(result.metadata or {})
    meta["fanout_stores"] = fanout_stores
    meta["unavailable_stores"] = unavailable_stores
    result.metadata = meta
    return result
