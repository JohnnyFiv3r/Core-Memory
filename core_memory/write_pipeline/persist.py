from __future__ import annotations

"""Legacy extraction persistence via CLI (compatibility path)."""

import subprocess
import sys
from pathlib import Path

from .normalize import normalize_bead_for_cli


def write_beads_via_cli(beads: list[dict], root: str) -> tuple[int, int]:
    """Write beads through canonical CLI add path.

    Returns (written, failed).
    """
    written = 0
    failed = 0
    script_dir = Path(__file__).resolve().parents[2]

    for bead in beads:
        b = normalize_bead_for_cli(bead)
        cmd = [
            sys.executable,
            "-m",
            "core_memory.cli",
            "--root",
            root,
            "add",
            "--type",
            b["type"],
            "--title",
            b["title"],
        ]
        for s in b["summary"]:
            cmd.extend(["--summary", s])
        for t in b.get("tags", []):
            cmd.extend(["--tag", t])
        cmd.extend(["--scope", b["scope"], "--authority", b["authority"]])
        if "confidence" in b:
            cmd.extend(["--confidence", str(b["confidence"])])

        proc = subprocess.run(cmd, cwd=script_dir, capture_output=True, text=True)
        if proc.returncode == 0:
            written += 1
        else:
            failed += 1
    return written, failed
