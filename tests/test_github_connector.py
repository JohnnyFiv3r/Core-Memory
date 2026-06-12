import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.integrations.github import ingest_github_event
from core_memory.runtime.ingest.source_events import (
    SourceEventMapping,
    SourceEventRule,
    ingest_source_event,
)


def _repo(full_name="acme/widgets", default_branch="main"):
    return {"full_name": full_name, "default_branch": default_branch}


def _merged_pr_event(number=42, delivery="d-pr-42", merged=True, **overrides):
    event = {
        "action": "closed",
        "delivery_id": delivery,
        "repository": _repo(),
        "sender": {"login": "octocat", "type": "User"},
        "pull_request": {
            "number": number,
            "title": "Add retry logic to sync worker",
            "body": "Retries transient failures with backoff.",
            "merged": merged,
            "merged_at": "2026-06-12T18:00:00Z",
            "html_url": f"https://github.com/acme/widgets/pull/{number}",
            "user": {"login": "octocat"},
        },
    }
    event.update(overrides)
    return event


def _push_event(delivery="d-push-1", files=("docs/runbook.md",), head="abc123def456", **overrides):
    event = {
        "delivery_id": delivery,
        "ref": "refs/heads/main",
        "after": head,
        "repository": _repo(),
        "sender": {"login": "octocat", "type": "User"},
        "pusher": {"name": "octocat"},
        "head_commit": {"timestamp": "2026-06-12T18:30:00Z"},
        "commits": [{"id": head, "added": [], "modified": list(files)}],
    }
    event.update(overrides)
    return event


class TestGitHubAdmissionPolicy(unittest.TestCase):
    def test_merged_pr_writes_structured_observation(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_github_event(td, event_name="pull_request", event=_merged_pr_event())
            self.assertEqual("accepted", out["status"])
            self.assertEqual("merged_pull_request", out["rule"])
            self.assertEqual(1, out["created_count"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][out["bead_ids"][0]]
            self.assertEqual("structured_observation", bead["type"])
            self.assertEqual("github", bead["source_system"])
            self.assertEqual("acme/widgets#42", bead["source_record_id"])
            self.assertEqual("merged", bead["record_action"])

    def test_unmerged_close_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_github_event(td, event_name="pull_request", event=_merged_pr_event(merged=False))
            self.assertEqual("skipped", out["status"])
            self.assertIn("rule_declined", out["reason"])
            self.assertFalse((Path(td) / ".beads" / "index.json").exists())

    def test_unmapped_event_kind_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_github_event(td, event_name="watch", event={"action": "started", "repository": _repo()})
            self.assertEqual("skipped", out["status"])
            self.assertEqual("event_not_bead_worthy", out["reason"])

    def test_bot_events_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            event = _merged_pr_event(sender={"login": "dependabot[bot]", "type": "Bot"})
            out = ingest_github_event(td, event_name="pull_request", event=event)
            self.assertEqual("skipped", out["status"])

    def test_closed_issue_writes_record(self):
        with tempfile.TemporaryDirectory() as td:
            event = {
                "action": "closed",
                "delivery_id": "d-issue-7",
                "repository": _repo(),
                "sender": {"login": "octocat", "type": "User"},
                "issue": {
                    "number": 7,
                    "title": "Sync worker drops events under load",
                    "body": "",
                    "closed_at": "2026-06-12T17:00:00Z",
                    "html_url": "https://github.com/acme/widgets/issues/7",
                    "user": {"login": "hubber"},
                },
            }
            out = ingest_github_event(td, event_name="issues", event=event)
            self.assertEqual("accepted", out["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][out["bead_ids"][0]]
            self.assertEqual("structured_observation", bead["type"])
            self.assertEqual("closed", bead["record_action"])

    def test_published_release_writes_document_reference(self):
        with tempfile.TemporaryDirectory() as td:
            event = {
                "action": "published",
                "delivery_id": "d-rel-1",
                "repository": _repo(),
                "sender": {"login": "octocat", "type": "User"},
                "release": {
                    "tag_name": "v2.1.0",
                    "name": "Widgets 2.1",
                    "body": "## Highlights\n- retry logic",
                    "published_at": "2026-06-12T19:00:00Z",
                    "html_url": "https://github.com/acme/widgets/releases/tag/v2.1.0",
                    "author": {"login": "octocat"},
                },
            }
            out = ingest_github_event(td, event_name="release", event=event)
            self.assertEqual("accepted", out["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][out["bead_ids"][0]]
            self.assertEqual("document_reference", bead["type"])
            self.assertEqual("release_notes", bead["document_kind"])
            self.assertEqual("github:acme/widgets:release:v2.1.0", bead["document_id"])


class TestGitHubDocVersioning(unittest.TestCase):
    def test_doc_push_writes_document_reference_per_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_github_event(
                td,
                event_name="push",
                event=_push_event(files=("docs/runbook.md", "README.md", "src/main.py")),
            )
            self.assertEqual("accepted", out["status"])
            self.assertEqual(2, out["created_count"])  # .py file is not a doc
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            names = {idx["beads"][b]["document_name"] for b in out["bead_ids"]}
            self.assertEqual({"docs/runbook.md", "README.md"}, names)

    def test_second_doc_push_versions_via_supersession(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_github_event(td, event_name="push", event=_push_event())
            second = ingest_github_event(
                td,
                event_name="push",
                event=_push_event(delivery="d-push-2", head="fed654cba321"),
            )
            self.assertEqual("version_superseded", second["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            old = idx["beads"][first["bead_ids"][0]]
            new = idx["beads"][second["bead_ids"][0]]
            self.assertEqual("superseded", old["status"])
            self.assertIn(second["bead_ids"][0], old["superseded_by"])
            self.assertIn(first["bead_ids"][0], new["supersedes"])

    def test_feature_branch_push_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            out = ingest_github_event(
                td, event_name="push", event=_push_event(ref="refs/heads/feature/retry")
            )
            self.assertEqual("skipped", out["status"])

    def test_redelivery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_github_event(td, event_name="push", event=_push_event())
            replay = ingest_github_event(td, event_name="push", event=_push_event())
            self.assertEqual("already_exists", replay["status"])
            self.assertEqual(first["bead_ids"], replay["bead_ids"])


class TestGenericSourceEventEngine(unittest.TestCase):
    def test_first_matching_rule_wins_and_glob_kinds_match(self):
        seen = []

        def build(event):
            seen.append(event.get("marker"))
            return None

        mapping = SourceEventMapping(
            source_system="jira",
            rules=[SourceEventRule(name="any-issue", event_kinds=("jira:issue_*",), build=build)],
        )
        out = ingest_source_event(
            ".", event_kind="jira:issue_updated", event={"marker": 1}, mapping=mapping
        )
        self.assertEqual("skipped", out["status"])
        self.assertEqual([1], seen)
        self.assertEqual("rule_declined:any-issue", out["reason"])

    def test_builder_payload_inherits_mapping_source_system(self):
        with tempfile.TemporaryDirectory() as td:
            def build(event):
                return {
                    "data_type_flag": "relational",
                    "title": "Deal moved to closed-won",
                    "summary": ["Deal 991 closed-won."],
                    "source_id": "hubspot:portal-1",
                    "source_event_id": "hs-evt-1",
                    "source_table": "deals",
                    "source_record_id": "991",
                    "record_action": "closed_won",
                    "entities": ["Acme Corp"],
                    "as_of_timestamp": "2026-06-12T12:00:00Z",
                    "core_memory_unifying_id": "hubspot:deal:991",
                    "hydration_ref": {"store": "hubspot", "ref": "deals/991"},
                }

            mapping = SourceEventMapping(
                source_system="hubspot",
                rules=[SourceEventRule(name="deal-stage", event_kinds=("deal.propertyChange",), build=build)],
            )
            out = ingest_source_event(
                td, event_kind="deal.propertyChange", event={}, mapping=mapping, session_id="hubspot-source"
            )
            self.assertEqual("accepted", out["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][out["bead_ids"][0]]
            self.assertEqual("hubspot", bead["source_system"])
            self.assertEqual("structured_observation", bead["type"])


if __name__ == "__main__":
    unittest.main()
