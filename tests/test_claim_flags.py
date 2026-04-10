import os
import pytest
from unittest.mock import patch

def test_claim_layer_disabled_by_default():
    from core_memory.integrations.openclaw_flags import claim_layer_enabled
    with patch.dict(os.environ, {}, clear=True):
        # Remove CORE_MEMORY_CLAIM_LAYER if set
        env = {k: v for k, v in os.environ.items() if k != "CORE_MEMORY_CLAIM_LAYER"}
        with patch.dict(os.environ, env, clear=True):
            assert claim_layer_enabled() == False

def test_claim_layer_enabled_with_env():
    from core_memory.integrations.openclaw_flags import claim_layer_enabled
    with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_LAYER": "1"}):
        assert claim_layer_enabled() == True

def test_claim_extraction_mode_default_off():
    from core_memory.integrations.openclaw_flags import claim_extraction_mode
    with patch.dict(os.environ, {}, clear=True):
        env = {k: v for k, v in os.environ.items() if k != "CORE_MEMORY_CLAIM_EXTRACTION_MODE"}
        with patch.dict(os.environ, env, clear=True):
            assert claim_extraction_mode() == "off"

def test_claim_extraction_mode_heuristic():
    from core_memory.integrations.openclaw_flags import claim_extraction_mode
    with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_EXTRACTION_MODE": "heuristic"}):
        assert claim_extraction_mode() == "heuristic"

def test_claim_extraction_mode_invalid_returns_off():
    from core_memory.integrations.openclaw_flags import claim_extraction_mode
    with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_EXTRACTION_MODE": "invalid_value"}):
        assert claim_extraction_mode() == "off"

def test_claim_resolution_disabled_by_default():
    from core_memory.integrations.openclaw_flags import claim_resolution_enabled
    with patch.dict(os.environ, {}, clear=True):
        env = {k: v for k, v in os.environ.items() if k != "CORE_MEMORY_CLAIM_RESOLUTION"}
        with patch.dict(os.environ, env, clear=True):
            assert claim_resolution_enabled() == False

def test_claim_retrieval_boost_disabled_by_default():
    from core_memory.integrations.openclaw_flags import claim_retrieval_boost_enabled
    with patch.dict(os.environ, {}, clear=True):
        env = {k: v for k, v in os.environ.items() if k != "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST"}
        with patch.dict(os.environ, env, clear=True):
            assert claim_retrieval_boost_enabled() == False
