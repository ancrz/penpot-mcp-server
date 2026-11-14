"""Tests for API key encryption/decryption (AES-256-GCM + pgcrypto redaction)."""

from __future__ import annotations

import json
from pathlib import Path

from penpot_mcp.agent_gateway import PenpotAgentGateway
from penpot_mcp.services.crypto import (
    decrypt_secret,
    encrypt_secret,
    is_encrypted_secret,
    transform_credentials_keys,
)


def test_aes_gcm_roundtrip_and_tamper_resistance() -> None:
    secret = "sk-test-super-secret-key-987654"
    c1 = encrypt_secret(secret)
    c2 = encrypt_secret(secret)

    assert is_encrypted_secret(c1)
    assert c1 != c2  # Random 96-bit nonce per encryption
    assert secret not in c1
    assert decrypt_secret(c1) == secret
    assert decrypt_secret(c2) == secret

    # Tampered ciphertext must fail closed
    tampered = c1[:-4] + ("AAAA" if not c1.endswith("AAAA") else "BBBB")
    assert decrypt_secret(tampered) == ""

    # Legacy plaintext passes through cleanly
    assert decrypt_secret("legacy-plain-key") == "legacy-plain-key"


def test_transform_credentials_redact_and_encrypt() -> None:
    creds = {
        "custom-openai": {
            "name": "custom-openai",
            "provider": "openai",
            "api_key": "sk-live-1234567890abcd",
        }
    }
    encrypted = transform_credentials_keys(creds, mode="encrypt")
    assert is_encrypted_secret(encrypted["custom-openai"]["api_key"])
    assert encrypted["custom-openai"]["encrypted"] is True

    decrypted = transform_credentials_keys(encrypted, mode="decrypt")
    assert decrypted["custom-openai"]["api_key"] == "sk-live-1234567890abcd"

    redacted = transform_credentials_keys(creds, mode="redact")
    assert redacted["custom-openai"]["api_key"] == ""
    assert redacted["custom-openai"]["key_last4"] == "abcd"
    assert redacted["custom-openai"]["encrypted_in"] == "pgcrypto-aes256"


def test_gateway_disk_persistence_never_stores_plaintext_key(tmp_path: Path) -> None:
    cfg_file = tmp_path / "gateway_profiles.json"
    gw = PenpotAgentGateway()
    gw._config_path = str(cfg_file)
    gw.upsert_credential(
        name="vault-test",
        provider="anthropic",
        api_key="sk-ant-secret-token-4321",
        activate=True,
    )
    gw._save_persisted_state()

    raw_text = cfg_file.read_text(encoding="utf-8")
    assert "sk-ant-secret-token-4321" not in raw_text
    parsed = json.loads(raw_text)
    assert is_encrypted_secret(parsed["credentials"]["vault-test"]["api_key"])

    gw2 = PenpotAgentGateway()
    gw2._config_path = str(cfg_file)
    gw2._load_persisted_state()
    assert gw2.credentials["vault-test"]["api_key"] == "sk-ant-secret-token-4321"
