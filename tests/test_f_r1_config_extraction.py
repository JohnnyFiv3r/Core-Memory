"""F-R1 acceptance tests: config extraction of hardcoded domain tags and query expansions.

Verifies:
1. Personal strings are stripped from core_memory/ source (not in config/defaults or tests).
2. Config loader merges shipped defaults with user overrides (user wins on conflict).
3. Shipped defaults produce the same domain tags as the old hardcoded code for library terms.
4. Query expansion with config exercises the same code paths as old hardcoded maps.
5. No user config present → falls back to shipped defaults only.
6. Precedence chain: CORE_MEMORY_CONFIG_DIR > {root}/config/ > ~/.core-memory/config/.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from core_memory.config.loader import (
    _DEFAULTS_DIR,
    _load_yaml,
    _merge_deep,
    get_config_dir,
    load_domain_tags,
    load_query_expansions,
)
from core_memory.retrieval.rerank import _infer_domain_tags_from_text, rerank_candidates
from core_memory.retrieval.query_norm import _expand_query_tokens, _tokenize


class TestPersonalStringsStripped(unittest.TestCase):
    """Grep verification: no personal/author-specific strings in core_memory/ source."""

    PERSONAL_TERMS = [
        "disney", "genie", "tiana", "magic kingdom", "fantasyland", "pirates",
        "cloudflare", "18888", "8788",
    ]

    def test_no_personal_strings_in_source(self):
        core_dir = Path(__file__).parents[1] / "core_memory"
        for term in self.PERSONAL_TERMS:
            result = subprocess.run(
                ["grep", "-ri", "--include=*.py", "--include=*.yaml", term, str(core_dir)],
                capture_output=True, text=True,
            )
            # Filter out config/defaults and test fixtures
            hits = [
                line for line in result.stdout.strip().splitlines()
                if line and "config/defaults/" not in line and "__pycache__" not in line
            ]
            self.assertEqual(hits, [], f"Personal term '{term}' found in core_memory/: {hits}")


class TestConfigLoaderDefaults(unittest.TestCase):
    """Shipped defaults load correctly and contain expected library terms."""

    def test_shipped_domain_tags_exist(self):
        tags = load_domain_tags()
        self.assertIn("core_memory_pipeline", tags)
        self.assertIn("retrieval_quality", tags)
        self.assertIn("process_management", tags)

    def test_shipped_domain_tags_no_personal(self):
        tags = load_domain_tags()
        self.assertNotIn("disney_planner", tags)
        self.assertNotIn("infra_network", tags)

    def test_shipped_core_memory_pipeline_matchers(self):
        tags = load_domain_tags()
        matchers = tags["core_memory_pipeline"]
        for expected in ["bead", "compaction", "retrieval", "flush"]:
            self.assertIn(expected, matchers)

    def test_shipped_query_expansions_exist(self):
        exps = load_query_expansions()
        self.assertIn("phrase_map", exps)
        self.assertIn("token_map", exps)
        self.assertIn("openclaw", exps["token_map"])
        self.assertIn("pydanticai", exps["token_map"])
        self.assertIn("springai", exps["token_map"])

    def test_no_root_falls_back_to_defaults(self):
        tags = load_domain_tags(root=None)
        self.assertIn("core_memory_pipeline", tags)
        exps = load_query_expansions(root=None)
        self.assertIn("openclaw", exps["token_map"])


class TestConfigLoaderMerge(unittest.TestCase):
    """User overrides merge correctly with shipped defaults."""

    def test_user_wins_on_key_conflict(self):
        base = {"a": [1, 2], "b": [3]}
        override = {"a": [10, 20, 30]}
        merged = _merge_deep(base, override)
        self.assertEqual(merged["a"], [10, 20, 30])
        self.assertEqual(merged["b"], [3])

    def test_user_adds_new_keys(self):
        base = {"a": [1]}
        override = {"b": [2]}
        merged = _merge_deep(base, override)
        self.assertEqual(merged["a"], [1])
        self.assertEqual(merged["b"], [2])

    def test_user_domain_tags_merge_with_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "domain_tags.yaml").write_text(
                "domain_tags:\n  my_project:\n    - 'foo'\n    - 'bar'\n",
                encoding="utf-8",
            )
            tags = load_domain_tags(root=Path(td))
            # User tag present
            self.assertIn("my_project", tags)
            self.assertEqual(tags["my_project"], ["foo", "bar"])
            # Shipped defaults still present
            self.assertIn("core_memory_pipeline", tags)

    def test_user_query_expansions_merge_with_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "query_expansions.yaml").write_text(
                "phrase_map:\n  'my phrase':\n    - 'x'\n    - 'y'\ntoken_map:\n  'mytoken':\n    - 'z'\n",
                encoding="utf-8",
            )
            exps = load_query_expansions(root=Path(td))
            self.assertEqual(exps["phrase_map"]["my phrase"], ["x", "y"])
            self.assertEqual(exps["token_map"]["mytoken"], ["z"])
            # Shipped defaults still present
            self.assertIn("openclaw", exps["token_map"])

    def test_user_override_wins_on_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "domain_tags.yaml").write_text(
                "domain_tags:\n  core_memory_pipeline:\n    - 'custom_only'\n",
                encoding="utf-8",
            )
            tags = load_domain_tags(root=Path(td))
            # User override replaces shipped default for this key
            self.assertEqual(tags["core_memory_pipeline"], ["custom_only"])


class TestConfigPrecedence(unittest.TestCase):
    """Precedence chain: env var > root/config > ~/.core-memory/config."""

    def test_env_var_takes_precedence(self):
        with tempfile.TemporaryDirectory() as env_dir, \
             tempfile.TemporaryDirectory() as root_dir:
            # Set up both locations
            Path(env_dir).mkdir(exist_ok=True)
            (Path(root_dir) / "config").mkdir()

            old_env = os.environ.get("CORE_MEMORY_CONFIG_DIR")
            try:
                os.environ["CORE_MEMORY_CONFIG_DIR"] = env_dir
                result = get_config_dir(root=Path(root_dir))
                self.assertEqual(result, Path(env_dir))
            finally:
                if old_env is None:
                    os.environ.pop("CORE_MEMORY_CONFIG_DIR", None)
                else:
                    os.environ["CORE_MEMORY_CONFIG_DIR"] = old_env

    def test_root_config_used_when_no_env(self):
        with tempfile.TemporaryDirectory() as root_dir:
            config_dir = Path(root_dir) / "config"
            config_dir.mkdir()

            old_env = os.environ.pop("CORE_MEMORY_CONFIG_DIR", None)
            try:
                result = get_config_dir(root=Path(root_dir))
                self.assertEqual(result, config_dir)
            finally:
                if old_env is not None:
                    os.environ["CORE_MEMORY_CONFIG_DIR"] = old_env

    def test_no_config_dir_returns_none_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as root_dir:
            old_env = os.environ.pop("CORE_MEMORY_CONFIG_DIR", None)
            old_home = os.environ.get("HOME")
            try:
                # Point HOME to a dir without .core-memory/config
                os.environ["HOME"] = root_dir
                result = get_config_dir(root=Path(root_dir))
                # root_dir/config doesn't exist, HOME/.core-memory/config doesn't exist
                self.assertIsNone(result)
            finally:
                if old_env is not None:
                    os.environ["CORE_MEMORY_CONFIG_DIR"] = old_env
                if old_home is not None:
                    os.environ["HOME"] = old_home


class TestDomainTagInference(unittest.TestCase):
    """Config-driven domain tag inference produces correct results."""

    def test_core_memory_text_tags_correctly(self):
        tags = load_domain_tags()
        result = _infer_domain_tags_from_text("bead compaction and retrieval", tags)
        self.assertIn("core_memory_pipeline", result)

    def test_unknown_text_gets_unknown_tag(self):
        tags = load_domain_tags()
        result = _infer_domain_tags_from_text("purple elephants dancing quietly", tags)
        self.assertEqual(result, {"unknown"})

    def test_user_domain_tag_fires(self):
        custom_tags = {"my_domain": ["special_term", "another_term"]}
        result = _infer_domain_tags_from_text("this has special_term in it", custom_tags)
        self.assertIn("my_domain", result)

    def test_multiple_tags_can_fire(self):
        tags = load_domain_tags()
        result = _infer_domain_tags_from_text("bead rerank structural grounding", tags)
        self.assertIn("core_memory_pipeline", result)
        self.assertIn("retrieval_quality", result)


class TestQueryExpansionWithConfig(unittest.TestCase):
    """Config-driven query expansion exercises the same code paths."""

    def test_openclaw_phrase_expansion(self):
        tokens = _tokenize("openclaw only")
        result = _expand_query_tokens("openclaw only", tokens)
        self.assertIn("adapter", result)
        self.assertIn("migration", result)
        self.assertIn("orchestrator", result)

    def test_pydanticai_token_expansion(self):
        tokens = _tokenize("pydanticai")
        result = _expand_query_tokens("pydanticai", tokens)
        self.assertIn("adapter", result)
        self.assertIn("integration", result)

    def test_springai_token_expansion(self):
        tokens = _tokenize("springai")
        result = _expand_query_tokens("springai", tokens)
        self.assertIn("adapter", result)
        self.assertIn("integration", result)

    def test_emit_turn_finalized_expansion(self):
        # emit_turn_finalized fires via phrase_map entries like "core adapters"
        # or "multi orchestrator", not via token_map (since _tokenize splits on _).
        # Verify it works through the phrase path.
        tokens = _tokenize("core adapters")
        result = _expand_query_tokens("core adapters", tokens)
        self.assertIn("emit_turn_finalized", result)
        self.assertIn("adapter", result)

    def test_no_expansion_for_unknown_tokens(self):
        tokens = _tokenize("xyzzy foobar")
        result = _expand_query_tokens("xyzzy foobar", tokens)
        self.assertEqual(result, tokens)

    def test_max_extra_respected(self):
        tokens = _tokenize("openclaw only multi orchestrator core adapters")
        result = _expand_query_tokens("openclaw only multi orchestrator core adapters", tokens, max_extra=3)
        self.assertLessEqual(len(result), len(tokens) + 3)

    def test_user_expansions_fire(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "query_expansions.yaml").write_text(
                "phrase_map:\n  'hello world':\n    - 'greeting'\ntoken_map: {}\n",
                encoding="utf-8",
            )
            tokens = _tokenize("hello world")
            result = _expand_query_tokens("hello world", tokens, root=Path(td))
            self.assertIn("greeting", result)


class TestRerankerWithConfig(unittest.TestCase):
    """End-to-end: reranker uses config-loaded domain tags."""

    def test_reranker_runs_with_config(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory.persistence.store import MemoryStore
            s = MemoryStore(td)
            s.add_bead(
                type="decision", title="Bead compaction strategy",
                summary=["chose FIFO with forced latest"],
                session_id="s1", source_turn_ids=["t1"],
            )
            from core_memory.retrieval.hybrid import hybrid_lookup
            h = hybrid_lookup(Path(td), "compaction", k=3)
            rr = rerank_candidates(Path(td), "compaction", h.get("results") or [])
            self.assertTrue(rr.get("ok"))
            results = rr.get("results") or []
            if results:
                features = results[0].get("features", {})
                # core_memory_pipeline should fire for "compaction" query
                self.assertIn("core_memory_pipeline", features.get("query_domains", []))


if __name__ == "__main__":
    unittest.main()
