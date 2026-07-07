from __future__ import annotations

from pathlib import Path

import pytest

from core_memory.persistence import encryption


_ENCRYPTION_ENV = (
    "CORE_MEMORY_ENCRYPTION_KEY",
    "CORE_MEMORY_ENCRYPTION_PASSPHRASE",
    "CORE_MEMORY_ENCRYPTION_SALT",
)


def _clear_encryption_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENCRYPTION_ENV:
        monkeypatch.delenv(name, raising=False)


def test_encryption_compat_imports_without_configuration_and_preserves_plaintext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _clear_encryption_env(monkeypatch)

    assert encryption.is_encryption_enabled() is False
    assert encryption.encrypt_bytes(b"plain bead data") == b"plain bead data"
    assert encryption.decrypt_bytes(b"plain bead data") == b"plain bead data"
    assert encryption.encrypt_text("visible memory") == b"visible memory"
    assert encryption.decrypt_text(b"visible memory") == "visible memory"

    path = tmp_path / "nested" / "memory.txt"
    encryption.write_encrypted(path, "stored without encryption")

    assert path.read_bytes() == b"stored without encryption"
    assert encryption.read_encrypted(path) == "stored without encryption"


def test_encryption_compat_round_trips_with_explicit_fernet_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    pytest.importorskip("cryptography.fernet", reason="cryptography extra not installed")
    _clear_encryption_env(monkeypatch)
    monkeypatch.setenv("CORE_MEMORY_ENCRYPTION_KEY", encryption.generate_key())

    payload = b"sensitive bead payload"
    encrypted = encryption.encrypt_bytes(payload)

    assert encryption.is_encryption_enabled() is True
    assert encrypted != payload
    assert encrypted.startswith(b"CMENC1:")
    assert encryption.decrypt_bytes(encrypted) == payload

    path = tmp_path / "secure" / "memory.txt"
    encryption.write_encrypted(path, "stored with encryption")

    assert path.read_bytes().startswith(b"CMENC1:")
    assert encryption.read_encrypted(path) == "stored with encryption"


def test_encryption_compat_round_trips_with_passphrase(
    monkeypatch: pytest.MonkeyPatch,
):
    pytest.importorskip("cryptography.fernet", reason="cryptography extra not installed")
    _clear_encryption_env(monkeypatch)
    monkeypatch.setenv("CORE_MEMORY_ENCRYPTION_PASSPHRASE", "correct horse battery staple")
    monkeypatch.setenv("CORE_MEMORY_ENCRYPTION_SALT", "deterministic-test-salt")

    encrypted = encryption.encrypt_text("derived key payload")

    assert encrypted.startswith(b"CMENC1:")
    assert encryption.decrypt_text(encrypted) == "derived key payload"


def test_encrypted_payload_requires_configured_cipher(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("cryptography.fernet", reason="cryptography extra not installed")
    _clear_encryption_env(monkeypatch)
    monkeypatch.setenv("CORE_MEMORY_ENCRYPTION_KEY", encryption.generate_key())
    encrypted = encryption.encrypt_text("requires key")

    _clear_encryption_env(monkeypatch)

    with pytest.raises(RuntimeError, match="Encrypted file found but no encryption key configured"):
        encryption.decrypt_text(encrypted)
