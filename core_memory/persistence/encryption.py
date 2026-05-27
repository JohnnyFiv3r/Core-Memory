"""Transparent encryption at rest for Core Memory files.

Encrypts session JSONL and index files using Fernet (AES-128-CBC + HMAC-SHA256).
Key management via CORE_MEMORY_ENCRYPTION_KEY env var (base64-encoded Fernet key)
or CORE_MEMORY_ENCRYPTION_PASSPHRASE (derived via PBKDF2).

Usage:
    pip install core-memory[encryption]

    # Generate a key:
    from cryptography.fernet import Fernet
    print(Fernet.generate_key().decode())

    # Set in environment:
    export CORE_MEMORY_ENCRYPTION_KEY="your-fernet-key-here"

    # Or use a passphrase (derived via PBKDF2):
    export CORE_MEMORY_ENCRYPTION_PASSPHRASE="my-secret-passphrase"
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any

_MAGIC = b"CMENC1:"  # File header to detect encrypted files
_SALT_FILE = ".beads/.encryption_salt"


def _get_cipher() -> Any | None:
    """Get a Fernet cipher from environment configuration, or None if not configured."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    key = os.environ.get("CORE_MEMORY_ENCRYPTION_KEY", "").strip()
    if key:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)

    passphrase = os.environ.get("CORE_MEMORY_ENCRYPTION_PASSPHRASE", "").strip()
    if passphrase:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        salt = os.environ.get("CORE_MEMORY_ENCRYPTION_SALT", "core-memory-default-salt").encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        derived = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        return Fernet(derived)

    return None


def is_encryption_enabled() -> bool:
    """Check if encryption is configured."""
    return _get_cipher() is not None


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt data. Returns original data if encryption not configured."""
    cipher = _get_cipher()
    if cipher is None:
        return data
    encrypted = cipher.encrypt(data)
    return _MAGIC + encrypted


def decrypt_bytes(data: bytes) -> bytes:
    """Decrypt data. Returns original data if not encrypted."""
    if not data.startswith(_MAGIC):
        return data  # Not encrypted
    cipher = _get_cipher()
    if cipher is None:
        raise RuntimeError(
            "Encrypted file found but no encryption key configured. "
            "Set CORE_MEMORY_ENCRYPTION_KEY or CORE_MEMORY_ENCRYPTION_PASSPHRASE."
        )
    return cipher.decrypt(data[len(_MAGIC):])


def encrypt_text(text: str) -> bytes:
    """Encrypt text string."""
    return encrypt_bytes(text.encode("utf-8"))


def decrypt_text(data: bytes) -> str:
    """Decrypt to text string."""
    return decrypt_bytes(data).decode("utf-8")


def write_encrypted(path: Path, text: str) -> None:
    """Write text to file, encrypting if configured."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_text(text))


def read_encrypted(path: Path) -> str:
    """Read text from file, decrypting if needed."""
    return decrypt_text(path.read_bytes())


def generate_key() -> str:
    """Generate a new Fernet encryption key."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise ImportError("Encryption requires: pip install core-memory[encryption]")
    return Fernet.generate_key().decode()
