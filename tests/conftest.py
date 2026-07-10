"""Shared pytest configuration for Core Memory tests.

Sets CORE_MEMORY_SEMANTIC_AUTODRAIN=off globally so daemon autodrain threads
don't write to temporary directories during test teardown, which causes
`shutil.rmtree` to fail with "Directory not empty" on Linux.

Individual tests that explicitly test autodrain behavior use `patch.dict`
to set CORE_MEMORY_SEMANTIC_AUTODRAIN=on, overriding this fixture.

The broad historical suite also runs in the documented warn-mode compatibility
window. Hard-mode tests explicitly delete or override this variable so the
shipping default remains covered without turning every legacy fixture into an
authorship fixture.
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_semantic_autodrain(monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")
    monkeypatch.setenv("CORE_MEMORY_AGENT_AUTHORED_MODE", "warn")
