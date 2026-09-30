"""Download the EnterpriseRAG-Bench questions and only the documents they cite.

The full corpus is a 1.26 GB zip of ~500k documents. This reads the zip's
central directory over HTTP range requests (~80 MB), then fetches just the
gold documents (expected_doc_ids of questions.jsonl, 722 documents, ~5 MB).

    uv run python -m backend.knowledge_bases.enterprise_rag.prepare [--force]

Writes data/enterprise_rag/{questions.jsonl, corpus.csv}.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import struct
import sys
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from backend.knowledge_bases.base import DATA_DIR


RELEASE_URL = "https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0"
DOCUMENTS_ZIP_URL = f"{RELEASE_URL}/all_documents.zip"
QUESTIONS_URL = f"{RELEASE_URL}/questions.jsonl"
OUTPUT_DIR = DATA_DIR / "enterprise_rag"
QUESTIONS_FILE = OUTPUT_DIR / "questions.jsonl"
CORPUS_FILE = OUTPUT_DIR / "corpus.csv"
CORPUS_COLUMNS = ("doc_id", "source_type", "title", "path", "document")
DOC_ID_RE = re.compile(r"dsid_[0-9a-f]{32}")
LOCAL_HEADER = struct.Struct("<4sHHHHHIIIHH")
LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
# Local extra fields may be longer than the central directory says.
EXTRA_SLACK = 1024
FETCH_WORKERS = 8


class RemoteFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, for zipfile."""

    def __init__(self, client: httpx.Client, url: str) -> None:
        response = client.get(url, headers={"Range": "bytes=0-0"})
        response.raise_for_status()
        if response.status_code != 206:
            raise RuntimeError(f"Server ignores range requests: {url}")
        self._client = client
        self._url = str(response.url)  # the redirected, signed download URL
        self.size = int(response.headers["content-range"].rsplit("/", 1)[1])
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self.size}
        self._position = base[whence] + offset
        return self._position

    def fetch(self, start: int, length: int) -> bytes:
        end = min(start + length, self.size) - 1
        response = self._client.get(self._url, headers={"Range": f"bytes={start}-{end}"})
        response.raise_for_status()
        return response.content

    def readinto(self, buffer) -> int:
        if self._position >= self.size or not len(buffer):
            return 0
        data = self.fetch(self._position, len(buffer))
        buffer[: len(data)] = data
        self._position += len(data)
        return len(data)


def read_member(remote: RemoteFile, info: zipfile.ZipInfo) -> str:
    """Fetch one zip member with a single range request and decompress it."""

    name_length = len(info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437"))
    raw = remote.fetch(
        info.header_offset,
        LOCAL_HEADER.size + name_length + len(info.extra) + EXTRA_SLACK + info.compress_size,
    )
    header = LOCAL_HEADER.unpack_from(raw)
    if header[0] != LOCAL_HEADER_SIGNATURE:
        raise ValueError(f"Bad local header for {info.filename}")
    start = LOCAL_HEADER.size + header[9] + header[10]
    compressed = raw[start : start + info.compress_size]
    if len(compressed) != info.compress_size:
        raise ValueError(f"Short read for {info.filename}")
    if info.compress_type == zipfile.ZIP_STORED:
        data = compressed
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(compressed)
    else:
        raise ValueError(f"Unsupported compression {info.compress_type} for {info.filename}")
    if zlib.crc32(data) != info.CRC:
        raise ValueError(f"CRC mismatch for {info.filename}")
    return data.decode("utf-8")


def gold_doc_ids(questions_file: Path) -> set[str]:
    with questions_file.open(encoding="utf-8") as handle:
        return {
            doc_id
            for line in handle
            if line.strip()
            for doc_id in json.loads(line)["expected_doc_ids"]
        }


def corpus_row(info: zipfile.ZipInfo, doc_id: str, text: str) -> dict[str, str]:
    text = text.strip()
    title = text.splitlines()[0].strip() if text else ""
    return {
        "doc_id": doc_id,
        "source_type": info.filename.split("/", 1)[0],
        "title": title[:300],
        "path": info.filename,
        "document": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="Download again even if files exist")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        if args.force or not QUESTIONS_FILE.exists():
            response = client.get(QUESTIONS_URL)
            response.raise_for_status()
            QUESTIONS_FILE.write_bytes(response.content)
        gold = gold_doc_ids(QUESTIONS_FILE)
        print(f"{QUESTIONS_FILE}: {len(gold)} gold documents")

        if CORPUS_FILE.exists() and not args.force:
            print(f"{CORPUS_FILE} exists; pass --force to download it again")
            return 0

        remote = RemoteFile(client, DOCUMENTS_ZIP_URL)
        print(f"Reading the central directory of {remote.size / 1e9:.2f} GB zip...")
        archive = zipfile.ZipFile(io.BufferedReader(remote, buffer_size=8 << 20))
        members: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            match = DOC_ID_RE.search(info.filename)
            if not match or match.group(0) not in gold or info.is_dir():
                continue
            if match.group(0) in members:
                print(
                    f"warning: duplicate {match.group(0)}; keeping "
                    f"{members[match.group(0)].filename}, skipping {info.filename}",
                    file=sys.stderr,
                )
                continue
            members[match.group(0)] = info
        missing = sorted(gold - members.keys())
        for doc_id in missing:
            print(f"warning: {doc_id} not found in the zip", file=sys.stderr)

        ordered = sorted(members.items(), key=lambda item: item[1].header_offset)
        print(f"Fetching {len(ordered)} documents...")
        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            texts = list(pool.map(lambda item: read_member(remote, item[1]), ordered))

    rows = sorted(
        (corpus_row(info, doc_id, text) for (doc_id, info), text in zip(ordered, texts)),
        key=lambda row: (row["source_type"], row["doc_id"]),
    )
    temporary = CORPUS_FILE.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CORPUS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(CORPUS_FILE)

    by_source: dict[str, int] = {}
    for row in rows:
        by_source[row["source_type"]] = by_source.get(row["source_type"], 0) + 1
    print(f"{CORPUS_FILE}: {len(rows)} documents {dict(sorted(by_source.items()))}")
    if missing:
        print(f"{len(missing)} gold documents are missing", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
