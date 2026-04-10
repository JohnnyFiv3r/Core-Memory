import os
import pytest
import tempfile
from unittest.mock import patch

def test_extract_and_attach_claims_flag_off():
    from core_memory.claim.turn_integration import extract_and_attach_claims
    with patch.dict(os.environ, {}, clear=False):
        # Ensure flag is off
        with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=False):
            result = extract_and_attach_claims("/tmp", "s1", "t1", ["b1"], {"user_query": "I love jazz"})
    assert result["claims_extracted"] == 0
    assert result["claims_written"] == 0

def test_extract_and_attach_claims_mode_off():
    from core_memory.claim.turn_integration import extract_and_attach_claims
    with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=True):
        with patch("core_memory.claim.turn_integration.claim_extraction_mode", return_value="off"):
            result = extract_and_attach_claims("/tmp", "s1", "t1", ["b1"], {"user_query": "I love jazz"})
    assert result["claims_extracted"] == 0

def test_extract_and_attach_claims_heuristic(tmp_path):
    from core_memory.claim.turn_integration import extract_and_attach_claims
    with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=True):
        with patch("core_memory.claim.turn_integration.claim_extraction_mode", return_value="heuristic"):
            result = extract_and_attach_claims(
                str(tmp_path), "s1", "t1", ["bead1"],
                {"user_query": "I prefer Python over Java", "assistant_final": ""}
            )
    # May or may not extract depending on heuristics, but should not error
    assert "claims_extracted" in result
    assert "claims_written" in result
    assert isinstance(result["bead_ids"], list)

def test_extract_and_attach_empty_query(tmp_path):
    from core_memory.claim.turn_integration import extract_and_attach_claims
    with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=True):
        with patch("core_memory.claim.turn_integration.claim_extraction_mode", return_value="heuristic"):
            result = extract_and_attach_claims(str(tmp_path), "s1", "t1", [], {})
    assert result["claims_written"] == 0
