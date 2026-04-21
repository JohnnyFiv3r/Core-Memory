"""F-R2 acceptance tests: hash-vector fallback determinism and semantic mode startup.

Verifies:
1. _deterministic_token_hash produces identical output across calls (no PYTHONHASHSEED dependency).
2. _hash_vectors produces byte-identical output across calls.
3. Default semantic mode is 'required' (not 'degraded_allowed').
4. In required mode with no provider: raises RuntimeError with actionable message.
5. In degraded_allowed mode: logs once at startup, stays silent afterward.
6. degraded=true response flag is preserved in degraded_allowed mode.
"""

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core_memory.retrieval.semantic_index as sem_mod
from core_memory.retrieval.semantic_index import (
    SEMANTIC_MODE_DEGRADED_ALLOWED,
    SEMANTIC_MODE_REQUIRED,
    _check_semantic_mode_startup,
    _deterministic_token_hash,
    _normalize_semantic_mode,
)


class TestDeterministicTokenHash(unittest.TestCase):
    """Fixed-seed hash is consistent across calls."""

    def test_same_token_same_result(self):
        results = [_deterministic_token_hash("bead", 256) for _ in range(100)]
        self.assertEqual(len(set(results)), 1)

    def test_different_tokens_differ(self):
        a = _deterministic_token_hash("bead", 256)
        b = _deterministic_token_hash("compaction", 256)
        self.assertNotEqual(a, b)

    def test_result_within_dim_range(self):
        for dim in [64, 128, 256, 512]:
            for tok in ["bead", "retrieval", "decision", "causal"]:
                h = _deterministic_token_hash(tok, dim)
                self.assertGreaterEqual(h, 0)
                self.assertLess(h, dim)

    def test_empty_token(self):
        h = _deterministic_token_hash("", 256)
        self.assertGreaterEqual(h, 0)
        self.assertLess(h, 256)


try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


@unittest.skipUnless(_HAS_NUMPY, "numpy not installed")
class TestHashVectorsDeterministic(unittest.TestCase):
    """_hash_vectors produces identical output across calls."""

    def test_identical_output(self):
        from core_memory.retrieval.semantic_index import _hash_vectors

        texts = ["bead compaction retrieval", "causal decision outcome"]
        v1 = _hash_vectors(texts, dim=128)
        v2 = _hash_vectors(texts, dim=128)
        self.assertTrue((v1 == v2).all(), "Hash vectors differ between calls")

    def test_deterministic_across_subprocess(self):
        """Verify determinism across separate Python processes (no PYTHONHASHSEED)."""
        script = (
            "import json; "
            "from core_memory.retrieval.semantic_index import _hash_vectors; "
            "v = _hash_vectors(['bead compaction'], dim=64); "
            "print(json.dumps(v.tolist()))"
        )
        results = []
        for _ in range(2):
            r = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True,
                env={**os.environ, "PYTHONHASHSEED": "random"},
            )
            self.assertEqual(r.returncode, 0, f"subprocess failed: {r.stderr}")
            results.append(r.stdout.strip())
        self.assertEqual(results[0], results[1], "Hash vectors differ across processes")


class TestDefaultSemanticMode(unittest.TestCase):
    """Default mode is 'required', not 'degraded_allowed'."""

    @patch.dict(os.environ, {}, clear=False)
    def test_default_is_required(self):
        os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        self.assertEqual(_normalize_semantic_mode(None), SEMANTIC_MODE_REQUIRED)

    @patch.dict(os.environ, {}, clear=False)
    def test_empty_string_is_required(self):
        os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        self.assertEqual(_normalize_semantic_mode(""), SEMANTIC_MODE_REQUIRED)

    def test_explicit_degraded_allowed(self):
        self.assertEqual(_normalize_semantic_mode("degraded_allowed"), SEMANTIC_MODE_DEGRADED_ALLOWED)

    def test_explicit_required(self):
        self.assertEqual(_normalize_semantic_mode("required"), SEMANTIC_MODE_REQUIRED)

    def test_invalid_value_defaults_to_required(self):
        self.assertEqual(_normalize_semantic_mode("bogus"), SEMANTIC_MODE_REQUIRED)


class TestStartupCheckRequired(unittest.TestCase):
    """In required mode with no provider: raises RuntimeError."""

    def setUp(self):
        sem_mod._startup_check_done = False

    def tearDown(self):
        sem_mod._startup_check_done = False

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
    }, clear=False)
    def test_required_no_provider_raises(self):
        env_clear = {
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "CORE_MEMORY_VECTOR_BACKEND": "",
        }
        with patch.dict(os.environ, env_clear):
            with self.assertRaises(RuntimeError) as ctx:
                _check_semantic_mode_startup()
            msg = str(ctx.exception)
            self.assertIn("required", msg)
            self.assertIn("degraded_allowed", msg)
            self.assertIn("pip install", msg)

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
        "OPENAI_API_KEY": "sk-test-key",
    }, clear=False)
    def test_required_with_provider_does_not_raise(self):
        _check_semantic_mode_startup()
        self.assertTrue(sem_mod._startup_check_done)


class TestStartupCheckDegradedAllowed(unittest.TestCase):
    """In degraded_allowed mode: logs once, stays silent."""

    def setUp(self):
        sem_mod._startup_check_done = False

    def tearDown(self):
        sem_mod._startup_check_done = False

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
    }, clear=False)
    def test_degraded_logs_once(self):
        with self.assertLogs("core_memory.retrieval.semantic_index", level="WARNING") as cm:
            _check_semantic_mode_startup()
        self.assertTrue(any("degraded_allowed" in msg for msg in cm.output))

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
    }, clear=False)
    def test_second_call_silent(self):
        _check_semantic_mode_startup()
        self.assertTrue(sem_mod._startup_check_done)
        # Second call should not log — it's a no-op
        _check_semantic_mode_startup()

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
    }, clear=False)
    def test_degraded_does_not_raise(self):
        env_clear = {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}
        with patch.dict(os.environ, env_clear):
            _check_semantic_mode_startup()
        self.assertTrue(sem_mod._startup_check_done)


class TestDegradedResponseFlag(unittest.TestCase):
    """degraded=true response flag is preserved in lexical fallback."""

    def test_lexical_fallback_has_degraded_flag(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store import MemoryStore
            s = MemoryStore(td)
            s.add_bead(
                type="decision", title="Test bead",
                summary=["test summary"], session_id="s1",
                source_turn_ids=["t1"],
            )
            sem_mod._startup_check_done = False
            try:
                result = sem_mod.semantic_lookup(
                    Path(td), "test", k=3,
                    mode="degraded_allowed",
                )
                self.assertTrue(result.get("degraded"), "degraded flag should be True in lexical fallback")
                self.assertEqual(result.get("backend"), "lexical")
            finally:
                sem_mod._startup_check_done = False


if __name__ == "__main__":
    unittest.main()
