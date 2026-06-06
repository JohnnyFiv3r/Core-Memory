from pathlib import Path


def test_public_repo_has_no_deployment_specific_memory_runtime_name():
    banned = "Sato" + "rid"
    roots = [Path("core_memory"), Path("docs"), Path("tests"), Path("demo")]
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
