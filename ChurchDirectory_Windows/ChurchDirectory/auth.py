"""
auth.py — Credential storage and retrieval.

Primary:  OS keychain via keyring (macOS Keychain / Windows Credential Manager).
Fallback: AES-256-GCM encryption using a machine-derived key, stored in a local
          encrypted file. Used when keyring is unavailable (locked-down environments).

Credentials are NEVER written to plain text files.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Optional

from models import Credentials
from errors import CredentialsNotFoundError

logger = logging.getLogger(__name__)

# ── Keyring username constants ────────────────────────────────────────────────
_APP_ID_USER = "planningcenter_app_id"
_PAT_USER    = "planningcenter_pat"

# ── Fallback encrypted credential file path ───────────────────────────────────
def _fallback_path() -> Path:
    if getattr(__import__('sys'), 'frozen', False):
        base = Path(__import__('sys').executable).parent
    else:
        base = Path(__file__).parent
    return base / ".credentials.enc"


def _machine_key() -> bytes:
    """
    Derive a 32-byte encryption key from the machine's UUID.
    Not a substitute for a proper HSM, but better than plain text.
    """
    try:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.check_output(
                "wmic csproduct get uuid", shell=True
            ).decode().split("\n")[1].strip()
            machine_id = result
        else:
            machine_id = str(uuid.getnode())
    except Exception:
        machine_id = str(uuid.getnode())

    salt = b"ChurchDirGen_v1_salt_2026"
    raw  = (machine_id + salt.decode()).encode()
    # Use SHA-256 to get a 32-byte key
    import hashlib
    return hashlib.sha256(raw).digest()


# ── Keyring operations ────────────────────────────────────────────────────────

def _keyring_available() -> bool:
    try:
        import keyring
        # Test that a backend is actually available
        keyring.get_keyring()
        return True
    except Exception:
        return False


def _keyring_get(service: str, username: str) -> Optional[str]:
    try:
        import keyring
        return keyring.get_password(service, username)
    except Exception:
        return None


def _keyring_set(service: str, username: str, password: str) -> bool:
    try:
        import keyring
        keyring.set_password(service, username, password)
        return True
    except Exception:
        return False


def _keyring_delete(service: str, username: str) -> None:
    try:
        import keyring
        keyring.delete_password(service, username)
    except Exception:
        pass


# ── AES-256-GCM fallback ──────────────────────────────────────────────────────

def _aes_encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt plaintext string. Returns base64-encoded ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os as _os
    nonce = _os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _aes_decrypt(ciphertext_b64: str, key: bytes) -> str:
    """Decrypt base64-encoded ciphertext. Returns plaintext string."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw   = base64.b64decode(ciphertext_b64)
    nonce = raw[:12]
    ct    = raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()


def _fallback_save(credentials: Credentials) -> None:
    key = _machine_key()
    data = json.dumps({"app_id": credentials.app_id, "pat": credentials.pat})
    encrypted = _aes_encrypt(data, key)
    _fallback_path().write_text(encrypted, encoding="utf-8")
    logger.warning(
        "Credentials saved using AES-256 fallback store (OS keychain unavailable). "
        "Consider running on a machine with keyring support for better security."
    )


def _fallback_load() -> Optional[Credentials]:
    path = _fallback_path()
    if not path.exists():
        return None
    try:
        key       = _machine_key()
        encrypted = path.read_text(encoding="utf-8")
        data      = json.loads(_aes_decrypt(encrypted, key))
        return Credentials(app_id=data["app_id"], pat=data["pat"])
    except Exception as e:
        logger.error("Failed to decrypt fallback credentials: %s", e)
        return None


def _fallback_delete() -> None:
    path = _fallback_path()
    if path.exists():
        path.unlink()


# ── Public API ────────────────────────────────────────────────────────────────

def get_credentials(service: str) -> Credentials:
    """
    Retrieve credentials from OS keychain or fallback store.
    Raises CredentialsNotFoundError if no credentials are stored.
    """
    # Try keyring first
    if _keyring_available():
        app_id = _keyring_get(service, _APP_ID_USER)
        pat    = _keyring_get(service, _PAT_USER)
        if app_id and pat:
            return Credentials(app_id=app_id, pat=pat)

    # Try fallback
    creds = _fallback_load()
    if creds:
        return creds

    raise CredentialsNotFoundError()


def save_credentials(service: str, credentials: Credentials) -> bool:
    """
    Save credentials to OS keychain. Falls back to AES-256 encrypted file.
    Returns True if saved to keychain, False if fallback was used.
    """
    if _keyring_available():
        ok1 = _keyring_set(service, _APP_ID_USER, credentials.app_id)
        ok2 = _keyring_set(service, _PAT_USER,    credentials.pat)
        if ok1 and ok2:
            logger.info("Credentials saved to OS keychain.")
            return True

    # Keyring failed — use fallback
    _fallback_save(credentials)
    return False


def delete_credentials(service: str) -> None:
    """Remove all stored credentials (both keychain and fallback)."""
    _keyring_delete(service, _APP_ID_USER)
    _keyring_delete(service, _PAT_USER)
    _fallback_delete()


def credentials_exist(service: str) -> bool:
    """Return True if any credentials are stored."""
    try:
        get_credentials(service)
        return True
    except Exception:
        return False
