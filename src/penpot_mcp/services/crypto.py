"""API Key encryption/decryption helpers for Penpot MCP Gateway.

Provides:
1. Master key resolution (explicit PENPOT_MCP_ENCRYPTION_KEY, HKDF-SHA256 from
   deployment secrets, or a local 0600 keyfile fallback — never hardcoded).
2. Local AES-256-GCM envelope encryption for the JSON fallback cache.
3. Shared credential-dictionary helpers for redaction and envelope encryption.
"""

from __future__ import annotations

import base64
import copy
import os
import secrets
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from penpot_mcp.config import settings

_ENC_PREFIX = "enc:aesgcm:v1:"
_LOCAL_KEYFILE = "/tmp/.penpot_mcp_vault.key"
_HKDF_SALT = b"penpot-mcp-ai-vault-v1"
_HKDF_INFO = b"penpot-mcp-api-key-aes256"


def _derive_aes256_key(material: bytes) -> bytes:
    """Derive a 32-byte AES-256 key using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return hkdf.derive(material)


@lru_cache(maxsize=1)
def get_vault_master_bytes() -> bytes:
    """Resolve the 32-byte master key for AES-256-GCM and pgcrypto passphrase derivation."""
    explicit = (settings.penpot_mcp_encryption_key or "").strip()
    if explicit:
        return _derive_aes256_key(explicit.encode("utf-8"))

    env_material = "|".join(
        part
        for part in (
            settings.penpot_db_pass.strip(),
            settings.ws_token.strip(),
            settings.penpot_password.strip(),
        )
        if part
    )
    if env_material:
        return _derive_aes256_key(env_material.encode("utf-8"))

    if os.path.exists(_LOCAL_KEYFILE):
        with open(_LOCAL_KEYFILE, "rb") as fh:
            raw = fh.read().strip()
        if len(raw) >= 32:
            return _derive_aes256_key(raw)

    generated = secrets.token_bytes(32)
    fd = os.open(_LOCAL_KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, base64.urlsafe_b64encode(generated))
    finally:
        os.close(fd)
    return _derive_aes256_key(base64.urlsafe_b64encode(generated))


def get_pgcrypto_passphrase() -> str:
    """Return a URL-safe base64 passphrase derived from the master key for pgcrypto."""
    return base64.urlsafe_b64encode(get_vault_master_bytes()).decode("ascii")


def is_encrypted_secret(value: str) -> bool:
    """Return True if value carries the AES-256-GCM envelope prefix."""
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string with AES-256-GCM (random 96-bit nonce)."""
    if not plaintext:
        return ""
    if is_encrypted_secret(plaintext):
        return plaintext
    nonce = os.urandom(12)
    ct = AESGCM(get_vault_master_bytes()).encrypt(nonce, plaintext.encode("utf-8"), None)
    token = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    """Decrypt an AES-256-GCM secret; returns legacy plaintext unchanged."""
    if not value:
        return ""
    if not is_encrypted_secret(value):
        return value
    raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX) :].encode("ascii"))
    if len(raw) < 13:
        return ""
    nonce, ct = raw[:12], raw[12:]
    try:
        pt = AESGCM(get_vault_master_bytes()).decrypt(nonce, ct, None)
        return pt.decode("utf-8")
    except Exception:
        return ""


def transform_credentials_keys(
    creds: dict[str, dict[str, Any]],
    *,
    mode: str,
) -> dict[str, dict[str, Any]]:
    """Deep-copy credentials dict and encrypt, decrypt, or redact `api_key` values."""
    out = copy.deepcopy(creds or {})
    for cred in out.values():
        if not isinstance(cred, dict):
            continue
        raw_key = str(cred.get("api_key") or "")
        if mode == "encrypt":
            if raw_key:
                cred["api_key"] = encrypt_secret(raw_key)
                cred["encrypted"] = True
        elif mode == "decrypt":
            if raw_key:
                cred["api_key"] = decrypt_secret(raw_key)
        elif mode == "redact":
            if raw_key:
                plain = decrypt_secret(raw_key)
                cred["key_last4"] = plain[-4:] if len(plain) >= 4 else "****"
                cred["api_key"] = ""
                cred["encrypted_in"] = "pgcrypto-aes256"
        else:
            raise ValueError(f"Unsupported credential transform mode: {mode}")
    return out
