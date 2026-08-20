from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken

# Connector config (Monero wallet-rpc host, Lightning macaroon, future exchange
# API keys) is encrypted at rest. The key lives outside the database — in the
# environment, never in a committed file (plan §82-83).


def _key() -> bytes:
    key = os.getenv("APP_SECRET_KEY") or os.getenv("BACKUP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("APP_SECRET_KEY (or BACKUP_ENCRYPTION_KEY) is not set; refusing to store connection secrets")
    return key.encode()


def encrypt_config(data: dict) -> str:
    return Fernet(_key()).encrypt(json.dumps(data).encode()).decode()


def decrypt_config(token: str) -> dict:
    try:
        return json.loads(Fernet(_key()).decrypt(token.encode()).decode())
    except InvalidToken as exc:
        raise ValueError("Stored connection config could not be decrypted") from exc
