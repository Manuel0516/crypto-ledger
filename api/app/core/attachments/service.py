from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.db.models import Attachment

ATTACHMENTS_DIR = Path(os.getenv("ATTACHMENTS_DIR", "data/attachments"))

# Supporting-document categories (plan §69).
VALID_KINDS = (
    "receipt",
    "invoice",
    "exchange_statement",
    "csv_export",
    "staking_statement",
    "payment_confirmation",
    "other",
)


def _get_key() -> bytes:
    # Reuses the same master key as encrypted backups (plan §69: "Evidence
    # files should live on encrypted storage") rather than introducing a
    # second key for the user to generate and keep safe.
    key = os.getenv("BACKUP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY is not set; refusing to store an unencrypted attachment")
    return key.encode()


def save_attachment(
    session: Session,
    content: bytes,
    filename: str,
    content_type: str,
    kind: str,
    event_id: int | None,
    description: str | None,
) -> Attachment:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    ciphertext = Fernet(_get_key()).encrypt(content)
    stored_name = f"{uuid.uuid4().hex}.enc"
    out_path = ATTACHMENTS_DIR / stored_name
    out_path.write_bytes(ciphertext)

    attachment = Attachment(
        event_id=event_id,
        kind=kind if kind in VALID_KINDS else "other",
        filename=filename,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(content),
        sha256=digest,
        storage_path=str(out_path),
        description=description,
    )
    session.add(attachment)
    session.flush()
    return attachment


def read_attachment_bytes(attachment: Attachment) -> bytes:
    ciphertext = Path(attachment.storage_path).read_bytes()
    try:
        plaintext = Fernet(_get_key()).decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("Attachment could not be decrypted") from exc
    if hashlib.sha256(plaintext).hexdigest() != attachment.sha256:
        raise ValueError("Attachment content does not match its recorded hash")
    return plaintext


def delete_attachment_file(attachment: Attachment) -> None:
    path = Path(attachment.storage_path)
    if path.exists():
        path.unlink()
