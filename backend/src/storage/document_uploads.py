"""Opaque document objects on local disk; no parsing, ingestion or conversation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import uuid4

from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024


def uploads_dir_from_env() -> Path:
    raw = os.getenv("DOCUMENT_UPLOAD_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else PROJECT_ROOT / "data" / "uploads"


class DocumentTooLarge(ValueError):
    pass


class StoredDocument(BaseModel):
    id: str
    kb: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    uploaded_at: datetime
    status: Literal["stored"] = "stored"


class DocumentUploadStore:
    """One UUID directory per object, published only after its metadata is saved.

    Docker mounts a named volume at the root; host-run APIs use data/uploads.
    Client filenames are metadata only and never participate in filesystem paths.
    """

    def __init__(self, root: Path | None = None, *, max_bytes: int = MAX_DOCUMENT_BYTES):
        self.root = (root if root is not None else uploads_dir_from_env()).resolve()
        self.max_bytes = max_bytes

    def save(self, kb: str, source: BinaryIO, *, filename: str, content_type: str) -> StoredDocument:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", kb):
            raise ValueError("Invalid knowledge base name")
        if not filename.strip() or len(filename) > 1024 or "\x00" in filename:
            raise ValueError("A filename of 1-1024 characters is required")
        document_id = uuid4().hex
        parent = self.root / kb
        parent.mkdir(parents=True, exist_ok=True)
        pending = Path(tempfile.mkdtemp(prefix=".upload-", dir=parent))
        try:
            size = 0
            digest = hashlib.sha256()
            with (pending / "content").open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise DocumentTooLarge(f"Document exceeds the {self.max_bytes}-byte limit")
                    target.write(chunk)
                    digest.update(chunk)
            if size == 0:
                raise ValueError("The document is empty")
            document = StoredDocument(
                id=document_id, kb=kb, filename=filename,
                content_type=content_type or "application/octet-stream",
                size_bytes=size, sha256=digest.hexdigest(),
                storage_key=f"{kb}/{document_id}/content",
                uploaded_at=datetime.now(timezone.utc),
            )
            (pending / "metadata.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")
            pending.rename(parent / document_id)
            return document
        finally:
            # Also removes partial files after disk errors or an interrupted copy.
            if pending.exists():
                shutil.rmtree(pending)
