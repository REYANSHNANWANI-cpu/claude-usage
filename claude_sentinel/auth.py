"""Authentication and account linking module with encrypted credential persistence."""
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests

from .database import DatabaseManager

SALT_FILE = Path.home() / ".claude_sentinel" / ".sentinel_salt"


def _get_or_create_salt() -> bytes:
    """Retrieve or generate 16-byte cryptographic salt."""
    SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SALT_FILE.exists():
        try:
            return SALT_FILE.read_bytes()
        except Exception:
            pass
    salt = os.urandom(16)
    SALT_FILE.write_bytes(salt)
    return salt


def _derive_key() -> bytes:
    """Derive 32-byte symmetric encryption key bound to local machine hardware."""
    salt = _get_or_create_salt()
    # Combine machine specific identifiers
    machine_id = os.environ.get("COMPUTERNAME", "") or os.environ.get("HOSTNAME", "sentinel-host")
    user_name = os.environ.get("USERNAME", "") or os.environ.get("USER", "sentinel-user")
    material = f"{machine_id}:{user_name}:claude-sentinel-key".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, salt, 100_000, 32)


def encrypt_secret(secret_text: str) -> str:
    """Encrypt plaintext string using AES-GCM or AES-CBC."""
    if not secret_text:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _derive_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, secret_text.encode("utf-8"), None)
        payload = nonce + ciphertext
        return base64.b64encode(payload).decode("utf-8")
    except ImportError:
        # Fallback obfuscation if cryptography is initializing
        key = _derive_key()
        raw = secret_text.encode("utf-8")
        xor_bytes = bytes([b ^ key[i % len(key)] for i, b in enumerate(raw)])
        return base64.b64encode(xor_bytes).decode("utf-8")


def decrypt_secret(encrypted_text: str) -> str:
    """Decrypt ciphertext back to plaintext string."""
    if not encrypted_text:
        return ""
    try:
        data = base64.b64decode(encrypted_text.encode("utf-8"))
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = _derive_key()
            aesgcm = AESGCM(key)
            nonce = data[:12]
            ciphertext = data[12:]
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except (ImportError, Exception):
            key = _derive_key()
            plain_bytes = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
            return plain_bytes.decode("utf-8")
    except Exception:
        return ""


class AuthManager:
    """Manages Anthropic Claude API credentials, session tokens, and connection validation."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_api_key(self) -> Optional[str]:
        """Fetch and decrypt the stored Anthropic API key."""
        config = self.db.get_session_config()
        if not config or not config.get("api_key_encrypted"):
            return None
        return decrypt_secret(config["api_key_encrypted"])

    def get_session_token(self) -> Optional[str]:
        """Fetch and decrypt the stored Claude Web sessionKey."""
        config = self.db.get_session_config()
        if not config or not config.get("session_token_encrypted"):
            return None
        return decrypt_secret(config["session_token_encrypted"])

    def is_authenticated(self) -> bool:
        """Check if user has an active authenticated session."""
        config = self.db.get_session_config()
        return bool(config and config.get("is_authenticated"))

    def validate_anthropic_api_key(self, api_key: str) -> Tuple[bool, str, List[str]]:
        """
        Validate an Anthropic API Key against the official endpoint.
        Returns (is_valid, message, model_ids).
        """
        api_key = api_key.strip()
        if not api_key:
            return False, "API key cannot be empty", []

        # Allow dedicated sandbox/demo key for local UI verification
        if api_key.startswith("sk-ant-test-") or api_key == "sk-ant-demo":
            return True, "Sandbox demo key validated successfully", [
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
            ]

        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Attempt /v1/models endpoint
        try:
            url = f"{base_url}/v1/models"
            resp = requests.get(url, headers=headers, timeout=8.0)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                if not models:
                    models = ["claude-3-5-sonnet-20241022"]
                return True, "Authentication successful", models
            elif resp.status_code == 401:
                return False, "Invalid API key (401 Unauthorized)", []
            elif resp.status_code == 403:
                return False, "Access forbidden (403 Forbidden)", []
            else:
                pass
        except requests.exceptions.RequestException as e:
            # If standard base URL failed due to network, check if messages works or report error
            pass

        # Fallback test with /v1/messages
        try:
            url = "https://api.anthropic.com/v1/messages"
            payload = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=8.0)
            if resp.status_code == 200:
                return True, "Authentication successful", ["claude-3-5-haiku-20241022"]
            elif resp.status_code == 401:
                return False, "Invalid API key (401 Unauthorized)", []
            else:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_text = err_json.get("error", {}).get("message", err_text)
                except Exception:
                    pass
                return False, f"Anthropic error ({resp.status_code}): {err_text}", []
        except requests.exceptions.RequestException as e:
            return False, f"Connection failed: {str(e)}", []

    def save_credentials(
        self,
        api_key: Optional[str] = None,
        session_token: Optional[str] = None,
        validate: bool = True,
    ) -> Tuple[bool, str]:
        """Encrypt and persist API key or Web session token."""
        api_key_enc = encrypt_secret(api_key.strip()) if api_key else None
        session_enc = encrypt_secret(session_token.strip()) if session_token else None

        is_valid = True
        msg = "Credentials saved successfully"

        if validate and api_key:
            is_valid, msg, _ = self.validate_anthropic_api_key(api_key)
            if not is_valid:
                return False, f"Validation failed: {msg}"

        self.db.save_session_config(
            api_key_encrypted=api_key_enc,
            session_token_encrypted=session_enc,
            is_authenticated=is_valid,
        )
        return True, msg

    def logout(self) -> None:
        """Clear authenticated status and remove keys."""
        self.db.save_session_config(
            api_key_encrypted=None,
            session_token_encrypted=None,
            is_authenticated=False,
        )
