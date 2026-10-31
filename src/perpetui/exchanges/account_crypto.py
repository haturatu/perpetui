from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import ChaCha20  # nosec B413

from perpetui.errors import CredentialsError


def _decrypt(value: str, command_path: str) -> str:
    if not value.startswith("enc_v1:"):
        return value
    try:
        nonce, payload = value[len("enc_v1:") :].split(":", 1)
        cipher = ChaCha20.new(
            key=hashlib.sha256(command_path.encode()).digest(),
            nonce=base64.urlsafe_b64decode(nonce),
        )
        result = cipher.decrypt(base64.urlsafe_b64decode(payload)).decode()
        if not result or not result.isprintable():
            raise ValueError("Invalid decrypted value")
        return result
    except (ValueError, UnicodeError) as exc:
        raise CredentialsError(
            "Cannot decrypt mexc-cli account. Use --mexc-command with the original installed "
            "mexc command path, or set MEXC_API_KEY and MEXC_API_SECRET."
        ) from exc
