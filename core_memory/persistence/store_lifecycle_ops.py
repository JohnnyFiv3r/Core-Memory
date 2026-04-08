from __future__ import annotations

from typing import Any


def close_store_for_store(store: Any) -> None:
    close_fn = getattr(store._backend, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def safe_del_for_store(store: Any) -> None:
    try:
        close_store_for_store(store)
    except Exception:
        pass


__all__ = ["close_store_for_store", "safe_del_for_store"]
