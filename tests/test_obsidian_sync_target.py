"""Tests for ObsidianSyncTarget (BeadSyncTarget implementation).

No mocking required — all tests use tmp_path and the real file system.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.obsidian


def _bead(bead_id: str = "bead-1", **kwargs) -> dict:
    return {
        "id": bead_id,
        "type": kwargs.get("type", "decision"),
        "title": kwargs.get("title", f"Title {bead_id}"),
        "status": kwargs.get("status", "open"),
        "session_id": kwargs.get("session_id", "sess-1"),
        "created_at": kwargs.get("created_at", "2026-01-01T00:00:00Z"),
        "summary": kwargs.get("summary", ["Summary line one.", "Summary line two."]),
    }


def _assoc(src: str = "bead-1", tgt: str = "bead-2") -> dict:
    return {
        "id": f"assoc-{src}-{tgt}",
        "source_bead": src,
        "target_bead": tgt,
        "relationship": "caused_by",
    }


class TestObsidianSyncTargetVault:
    def test_on_bead_written_creates_md_file(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        bead = _bead("bead-A", session_id="sess-X")
        st.on_bead_written(bead)

        md = tmp_path / "sess-X" / "bead-A.md"
        assert md.exists()
        content = md.read_text()
        assert "id: bead-A" in content
        assert "type: decision" in content
        assert "status: open" in content
        assert "# Title bead-A" in content
        assert "- Summary line one." in content

    def test_on_bead_written_frontmatter_fields(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        bead = _bead("bead-B", session_id="s1", created_at="2026-05-01T12:00:00Z")
        st.on_bead_written(bead)

        content = (tmp_path / "s1" / "bead-B.md").read_text()
        assert "created_at: 2026-05-01T12:00:00Z" in content
        assert "session_id: s1" in content

    def test_on_association_written_appends_wikilink(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        bead = _bead("bead-1", session_id="s1")
        st.on_bead_written(bead)
        st.on_association_written(_assoc("bead-1", "bead-2"))

        content = (tmp_path / "s1" / "bead-1.md").read_text()
        assert "[[bead-2]]" in content

    def test_on_association_written_no_duplicate_wikilink(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        st.on_bead_written(_bead("bead-1", session_id="s1"))
        st.on_association_written(_assoc("bead-1", "bead-2"))
        st.on_association_written(_assoc("bead-1", "bead-2"))

        content = (tmp_path / "s1" / "bead-1.md").read_text()
        assert content.count("[[bead-2]]") == 1

    def test_on_bead_retracted_updates_status_and_appends_notice(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        st.on_bead_written(_bead("bead-R", session_id="s1"))
        st.on_bead_retracted("bead-R")

        content = (tmp_path / "s1" / "bead-R.md").read_text()
        assert "status: retracted" in content
        assert "RETRACTED" in content

    def test_on_bead_retracted_no_double_notice(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        st.on_bead_written(_bead("bead-R", session_id="s1"))
        st.on_bead_retracted("bead-R")
        st.on_bead_retracted("bead-R")

        content = (tmp_path / "s1" / "bead-R.md").read_text()
        assert content.count("RETRACTED") == 1

    def test_sync_from_storage_writes_all_beads_and_associations(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        beads = [_bead(f"bead-{i}", session_id="s1") for i in range(3)]
        assocs = [_assoc("bead-0", "bead-1"), _assoc("bead-1", "bead-2")]
        result = st.sync_from_storage(beads=beads, associations=assocs)

        assert result["synced_beads"] == 3
        assert result["synced_associations"] == 2
        assert result["errors"] == []
        assert (tmp_path / "s1" / "bead-0.md").exists()
        assert (tmp_path / "s1" / "bead-2.md").exists()

    def test_search_candidates_no_rest_url_returns_not_ok(self, tmp_path):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        result = st.search_candidates("test query")
        assert result["ok"] is False
        assert "REST API not configured" in result["warnings"][0]

    def test_no_vault_path_all_methods_no_raise(self):
        from core_memory.integrations.obsidian import ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path="")
        st.on_bead_written(_bead("x"))
        st.on_association_written(_assoc("x", "y"))
        st.on_bead_retracted("x")
        result = st.sync_from_storage(beads=[_bead("z")], associations=[])
        assert "synced_beads" in result

    def test_bead_sync_target_protocol_satisfied(self, tmp_path):
        from core_memory.integrations.obsidian import BeadSyncTarget, ObsidianSyncTarget
        st = ObsidianSyncTarget(vault_path=str(tmp_path))
        assert isinstance(st, BeadSyncTarget)

    def test_persistence_provider_loads_obsidian_sync_target(self, tmp_path, monkeypatch):
        from core_memory.persistence.sync_targets import create_sync_targets

        monkeypatch.setenv("CORE_MEMORY_SYNC_TARGETS", "obsidian")
        monkeypatch.setenv("CORE_MEMORY_OBSIDIAN_VAULT", str(tmp_path))

        targets = create_sync_targets()

        assert [getattr(target, "name", "") for target in targets] == ["obsidian"]
