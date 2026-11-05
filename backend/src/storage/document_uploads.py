"""Opaque document objects on local disk; no parsing, ingestion or conversation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel

from backend.src.storage.file_library import FileBlobStore


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
    Source readers also resolve flat library keys under the separate library root.
    """

    def __init__(self, root: Path | None = None, *, max_bytes: int = MAX_DOCUMENT_BYTES,
                 library_root: Path | None = None):
        """Configure legacy upload storage and the library used by linked sources."""

        self.root = (root if root is not None else uploads_dir_from_env()).resolve()
        self.max_bytes = max_bytes
        self.library = FileBlobStore(library_root)

    def object_dir(self, kb: str, document_id: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", kb):
            raise ValueError("Invalid knowledge base name")
        return self.root / kb / UUID(document_id).hex

    def get(self, kb: str, document_id: str) -> StoredDocument:
        path = self.object_dir(kb, document_id)
        document = StoredDocument.model_validate_json((path / "metadata.json").read_text())
        if document.kb != kb or document.id != path.name or not (path / "content").is_file():
            raise ValueError("Invalid document metadata")
        return document

    def list(self, kb: str) -> list[StoredDocument]:
        # Ignore unpublished temporary uploads. A damaged object must be reported,
        # rather than silently disappearing from the management inventory.
        self.object_dir(kb, UUID(int=0).hex)
        parent = self.root / kb
        if not parent.exists():
            return []
        return sorted(
            (self.get(kb, path.name) for path in parent.iterdir()
             if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name)),
            key=lambda item: (item.uploaded_at, item.id), reverse=True,
        )

    def processing(self, kb: str, document_id: str) -> dict:
        self.get(kb, document_id)
        path = self.object_dir(kb, document_id) / "processing.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def update_processing(self, kb: str, document_id: str, state: dict) -> None:
        self.get(kb, document_id)
        self._write(self.object_dir(kb, document_id) / "processing.json",
                    json.dumps(state, ensure_ascii=False, indent=2))

    @staticmethod
    def _write(path: Path, text: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8", newline="")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def converted_text(self, kb: str, document_id: str) -> str | None:
        state = self.processing(kb, document_id)
        if state.get("conversion", {}).get("status") != "done":
            return None
        return (self.object_dir(kb, document_id) / "converted.txt").read_bytes().decode("utf-8")

    def convert_text(self, kb: str, document_id: str) -> dict:
        document = self.get(kb, document_id)
        state = self.processing(kb, document_id)
        if state.get("conversion", {}).get("status") == "done":
            return state
        if not text_convertible(document):
            raise ValueError("OCR/media conversion is not implemented for this file type")
        path = self.object_dir(kb, document_id)
        raw = (path / "content").read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        # Strict decoding: never replace unreadable bytes with invented text.
        text = raw.decode(encoding)
        if not text.strip() or "\x00" in text:
            raise ValueError("The file must contain readable, nonempty text")
        self._write(path / "converted.txt", text)
        state["conversion"] = {"status": "done", "encoding": encoding,
                               "sha256": hashlib.sha256(text.encode()).hexdigest(),
                               "at": datetime.now(timezone.utc).isoformat()}
        self.update_processing(kb, document_id, state)
        return state

    def save(
        self,
        kb: str,
        source: BinaryIO,
        *,
        filename: str,
        content_type: str,
        source_id: UUID | None = None,
    ) -> StoredDocument:
        """Stream original bytes into their final source-keyed object.

        Passing `source_id` uses the application-database layout and writes no
        metadata sidecar. Omitting it preserves the legacy object format used
        by benchmark and pre-cutover code.
        """

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", kb):
            raise ValueError("Invalid knowledge base name")
        if not filename.strip() or len(filename) > 1024 or "\x00" in filename:
            raise ValueError("A filename of 1-1024 characters is required")
        document_id = (source_id or uuid4()).hex
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
            if source_id is None:
                (pending / "metadata.json").write_text(
                    document.model_dump_json(indent=2), encoding="utf-8"
                )
            pending.rename(parent / document_id)
            return document
        finally:
            # Also removes partial files after disk errors or an interrupted copy.
            if pending.exists():
                shutil.rmtree(pending)

    def remove(self, storage_key: str) -> None:
        """Remove one source object after its database transaction failed."""

        parts = Path(storage_key).parts
        if len(parts) != 3 or parts[2] != "content":
            raise ValueError("Invalid document storage key")
        directory = self.object_dir(parts[0], parts[1])
        if directory.exists():
            shutil.rmtree(directory)

    def content_path(self, storage_key: str) -> Path:
        """Resolve legacy or library source keys without moving original bytes.

        Returns:
            A path under the configured upload or library root, without checking existence.

        Raises:
            ValueError: The key does not match either store's layout.
        """

        if re.fullmatch(r"[0-9a-f]{32}", storage_key):
            return self.library.path(storage_key)
        parts = Path(storage_key).parts
        if len(parts) != 3 or parts[2] != "content":
            raise ValueError("Invalid document storage key")
        return self.object_dir(parts[0], parts[1]) / "content"


def text_convertible(document: StoredDocument) -> bool:
    return (Path(document.filename).suffix.lower() in {
        ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
    } or document.content_type.startswith("text/"))
