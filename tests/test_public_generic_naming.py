from pathlib import Path


def test_public_repo_has_no_deployment_specific_memory_runtime_name():
    banned = "Sato" + "rid"
    roots = [
        Path("core_memory"),
        Path("docs"),
        Path("tests"),
        Path("demo"),
        Path("plugins"),
        Path("scripts"),
    ]
    hits = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if banned.lower() in text.lower():
                hits.append(str(path))
    assert hits == []


def test_graph_layer_does_not_import_runtime_modules():
    hits = []
    for path in Path("core_memory/graph").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "core_memory.runtime" in text:
            hits.append(str(path))
    assert hits == []


def test_retired_ragie_runtime_is_not_reintroduced_or_advertised():
    banned_literals = [
        "CORE_MEMORY_" + "RAGIE_API_KEY",
        "external_" + "ragie_api_key",
        "ragie_" + "adapter",
        "ragie_" + "cfg",
        "Ragie documents",
        "Ragie and PipeHouse",
        "Ragie (multi-modal)",
    ]
    allowed_paths = {
        Path("docs/PRD/ragie-fanout-removal.md"),
        Path("tests/test_public_generic_naming.py"),
    }
    roots = [Path("README.md"), Path("core_memory"), Path("docs"), Path("tests")]
    hits = []
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if path in allowed_paths or path.is_dir() or path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for literal in banned_literals:
                if literal in text:
                    hits.append(f"{path}:{literal}")
    assert hits == []
