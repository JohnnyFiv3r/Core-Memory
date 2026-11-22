"""Checks for score.py, on synthetic exports: no PostgreSQL, model or network.

    python3 -I eval/adversarial/test_score.py

Each test runs score.py as a command, as run.sh and people do. The files in
testdata/ are what score.py printed for the same exports before it refused
a message stored as more than one chunk.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCORE = HERE / "score.py"
TESTDATA = HERE / "testdata"

CASES = """
[[read]]
id = "R1"
probe = "decisions"
text = "We will ship on Friday."
expect = ["decision"]
why = "It settles the date."

[[thread]]
id = "T1"
probe = "a budget that changes"

[[thread.message]]
id = "a"
speaker = "Ann"
text = "Alice sets the budget at ten thousand."
expect = ["decision"]

[[thread.message]]
id = "b"
speaker = "Bo"
text = "Alicia raises the budget to twelve thousand."

[[thread.message]]
id = "c"
speaker = "Cy"
text = "The budget is approved."

[[thread.pair]]
a = "a"
b = "b"
expect = ["cause"]

[[thread.pair]]
a = "b"
b = "c"
expect = ["repl"]
why = "The raise replaces the first figure."

[[thread.names]]
same = ["Alice", "Alicia"]

[[thread.no_name]]
name = "budget"
"""

THREAD = """
[[thread]]
id = "{id}"
probe = "a thread of its own"

[[thread.message]]
id = "{message}"
speaker = "Ann"
text = "SECRET-TEXT-{message}"
"""


def event(scope, name):
    return {
        "record_kind": "event",
        "event_id": f"{scope}:e{name}",
        "key": {"scope": scope, "external_id": name},
    }


def chunk(scope, name, ordinal, text):
    return {
        "record_kind": "chunk",
        "chunk_id": f"{scope}:{name}{ordinal + 1}",
        "event_id": f"{scope}:e{name}",
        "ordinal": ordinal,
        "text": text,
    }


def typed(scope, name, key):
    return {
        "record_kind": "chunk_read",
        "chunk_id": f"{scope}:{name}1",
        "status": "typed",
        "type": {"key": key},
    }


def verdict(scope, earlier, later, said, arrow, direction, read_no=1):
    return {
        "record_kind": "verdict",
        "earlier_bead": f"{scope}:{earlier}",
        "later_bead": f"{scope}:{later}",
        "read_no": read_no,
        "verdict": said,
        "arrow": arrow,
        "direction": direction,
    }


def run(scope, a_chunks=1, verdict_from="a1", fuller=False, said="cause", r1="decision"):
    """One run of CASES: message a stored as `a_chunks` chunks, and the judge's
    first-read verdict on `verdict_from` to b's chunk. With `fuller`, also
    what fills the other sections of the report."""
    texts = {
        "a": "Alice sets the budget at ten thousand.",
        "b": "Alicia raises the budget to twelve thousand.",
        "c": "The budget is approved.",
        "R1": "We will ship on Friday.",
    }
    records = [event(scope, name) for name in texts]
    for name, text in texts.items():
        records.append(chunk(scope, name, 0, text))
    for ordinal in range(1, a_chunks):
        records.append(chunk(scope, "a", ordinal, "and then some more of it."))
    records.append(typed(scope, "R1", r1))
    records.append(typed(scope, "a", "decision"))
    records.append(verdict(scope, verdict_from, "b1", "leadsto", said, "earlier_to_later"))
    if fuller:
        records += [
            # Said of a pair no case names.
            verdict(scope, "a1", "c1", "leadsto", "enables", "earlier_to_later"),
            # A wrong answer, so that the pair's why is printed.
            verdict(scope, "b1", "c1", "none", None, None),
            # A second read of a named pair.
            verdict(scope, "a1", "b1", "leadsto", said, "earlier_to_later", read_no=2),
            {
                "record_kind": "edge",
                "from_bead": f"{scope}:b1",
                "to_bead": f"{scope}:c1",
                "arrow": "repl",
            },
            {
                "record_kind": "name_link",
                "a_bead": f"{scope}:a1",
                "a_start": 0,
                "a_end": 5,
                "b_bead": f"{scope}:b1",
                "b_start": 0,
                "b_end": 6,
            },
            {"record_kind": "bead", "bead_id": f"{scope}:g", "bead_kind": "group"},
            {
                "record_kind": "edge",
                "from_bead": f"{scope}:g",
                "to_bead": f"{scope}:a1",
                "arrow": "same-memory",
            },
        ]
    return records


def score(records, cases=CASES, flags=()):
    """What score.py returns, prints and complains of for an export."""
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        (folder / "cases.toml").write_text(cases, encoding="utf-8")
        (folder / "export.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
        done = subprocess.run(
            [sys.executable, "-I", str(SCORE), *flags, str(folder / "cases.toml"), str(folder / "export.jsonl")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    return done.returncode, done.stdout, done.stderr


def golden(name):
    return (TESTDATA / name).read_text(encoding="utf-8")


class OneChunkExportsScoreAsTheyAlwaysDid(unittest.TestCase):
    def test_a_one_chunk_export_prints_what_it_printed_before(self):
        code, out, err = score(run("adversarial-1"))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("- a → b: right. Said cause; right: cause", out)
        self.assertEqual(out, golden("one_chunk.md"))

    def test_every_section_of_a_one_run_report_is_unchanged(self):
        code, out, err = score(run("adversarial-1", fuller=True))
        self.assertEqual((code, err), (0, ""))
        for section in ("written, ending currency", "also said, unscored", "linked", "linked to nothing"):
            self.assertIn(section, out)
        self.assertEqual(out, golden("one_run.md"))

    def test_every_section_of_a_several_runs_report_is_unchanged(self):
        records = run("adversarial-1", fuller=True) + run("adversarial-2", fuller=True, said="result", r1="outcome")
        code, out, err = score(records)
        self.assertEqual((code, err), (0, ""))
        for section in ("pair by pair", "Group beads", "cases not right in every run"):
            self.assertIn(section, out)
        self.assertEqual(out, golden("two_runs.md"))


if __name__ == "__main__":
    unittest.main()
