"""Download a small, reproducible OCR benchmark from Hugging Face.

Layout produced under ``--out`` (default ``data/benchmark``)::

    benchmark/
    ├── manifest.json            sources, pinned revisions, seed, counts
    ├── handwriting/             IAM lines           (image + text)
    ├── handwritten_math/        CROHME 2019         (image + LaTeX)
    ├── chart/                   ChartQA test        (image + CSV table + QA pairs)
    ├── table/                   PubTabNet val       (image + HTML table)
    └── mixed_document/          DocLayNet test      (page image + layout boxes + cell text)

Each task folder holds ``images/`` and ``annotations.jsonl`` (one JSON object per sample).

Reproducibility: every source is pinned to a Hub commit SHA and samples are drawn with a
fixed seed, so re-running yields identical files. Only the parquet row groups / zip members
that contain the chosen samples are fetched, not the full datasets.

Usage::

    uv sync --group benchmark
    uv run python src/adapters/download_ocr_dataset.py [--n 200] [--seed 42] [--only chart table]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

ROOT = Path(__file__).resolve().parents[2]

# Pinned sources: (repo, revision, file inside repo). Changing any of these changes the benchmark.
SOURCES = {
    "handwriting": {
        "dataset": "IAM Handwriting (lines)",
        "repo": "Teklia/IAM-line",
        "revision": "fbdad97500ce54635c0d1ba306bf535cb40656cf",
        "file": "data/test.parquet",
        "license": "MIT (HF mirror); IAM terms of use apply",
    },
    "handwritten_math": {
        "dataset": "CROHME 2019 test",
        "repo": "Neeze/CROHME-full",
        "revision": "6d03ee8f86df1d0d93f3de00df75303d53b75f7d",
        "file": "data/2019-00000-of-00001.parquet",
        "license": "CROHME competition terms",
    },
    "chart": {
        "dataset": "ChartQA test",
        "repo": "ahmed-masry/ChartQA",
        "revision": "af8b6f5c08c95085271561c2a3f9d15f2b5a9031",
        "file": "ChartQA Dataset.zip",
        "license": "GPL-3.0",
    },
    "table": {
        "dataset": "PubTabNet validation",
        "repo": "apoidea/pubtabnet-html",
        "revision": "0d58324674ff93825c972f7fdfd2887e0ae0a247",
        "file": "data/validation-00000-of-00001.parquet",
        "license": "CDLA-Permissive-1.0",
    },
    "mixed_document": {
        "dataset": "DocLayNet v1.2 test (stratified by doc_category)",
        "repo": "docling-project/DocLayNet-v1.2",
        "revision": "0daf93102e2efce76c3e11a274a5e0d0969391d3",
        "file": [f"data/test-{i:05d}-of-00006.parquet" for i in range(6)],
        "license": "CDLA-Permissive-1.0",
    },
}

DOCLAYNET_LABELS = {
    1: "Caption", 2: "Footnote", 3: "Formula", 4: "List-item", 5: "Page-footer",
    6: "Page-header", 7: "Picture", 8: "Section-header", 9: "Table", 10: "Text", 11: "Title",
}

fs = HfFileSystem()


def hub_path(src: dict, file: str | None = None) -> str:
    return f"datasets/{src['repo']}@{src['revision']}/{file or src['file']}"


def image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    return ".bin"


def sample_indices(num_rows: int, n: int, seed: int) -> list[int]:
    return sorted(random.Random(seed).sample(range(num_rows), min(n, num_rows)))


def read_parquet_rows(path: str, wanted: list[int], columns: list[str] | None = None):
    """Yield (row_index, row_dict) for the given rows, fetching only the row groups that hold them."""
    with fs.open(path) as f:
        pf = pq.ParquetFile(f)
        start = 0
        for g in range(pf.metadata.num_row_groups):
            size = pf.metadata.row_group(g).num_rows
            idxs = [i for i in wanted if start <= i < start + size]
            if idxs:
                table = pf.read_row_group(g, columns=columns)
                for i in idxs:
                    yield i, table.slice(i - start, 1).to_pylist()[0]
            start += size


def iter_parquet_rows(src: dict, n: int, seed: int, columns: list[str] | None = None):
    """Yield (row_index, row_dict) for a seeded sample of ``src['file']``."""
    path = hub_path(src)
    with fs.open(path) as f:
        num_rows = pq.ParquetFile(f).metadata.num_rows
    yield from read_parquet_rows(path, sample_indices(num_rows, n, seed), columns)


def save_image(task_dir: Path, sample_id: str, data: bytes) -> str:
    rel = Path("images") / f"{sample_id}{image_ext(data)}"
    (task_dir / rel).write_bytes(data)
    return rel.as_posix()


def build_handwriting(task_dir: Path, src: dict, n: int, seed: int) -> list[dict]:
    records = []
    for i, row in iter_parquet_rows(src, n, seed):
        sid = f"iam_{i:05d}"
        records.append({"id": sid, "image": save_image(task_dir, sid, row["image"]["bytes"]),
                        "text": row["text"], "source_index": i})
    return records


def build_handwritten_math(task_dir: Path, src: dict, n: int, seed: int) -> list[dict]:
    records = []
    for i, row in iter_parquet_rows(src, n, seed):
        sid = f"crohme2019_{i:05d}"
        records.append({"id": sid, "image": save_image(task_dir, sid, row["image"]["bytes"]),
                        "latex": row["label"], "source_index": i})
    return records


def build_table(task_dir: Path, src: dict, n: int, seed: int) -> list[dict]:
    records = []
    for i, row in iter_parquet_rows(src, n, seed, columns=["image", "imgid", "html_table"]):
        sid = f"pubtabnet_{row['imgid']}"
        records.append({"id": sid, "image": save_image(task_dir, sid, row["image"]["bytes"]),
                        "html": row["html_table"], "source_index": i})
    return records


def build_mixed_document(task_dir: Path, src: dict, n: int, seed: int) -> list[dict]:
    # Test shards are grouped by doc_category, so sample an equal quota per category across all shards.
    by_category = defaultdict(list)
    for file in src["file"]:
        with fs.open(hub_path(src, file)) as f:
            metas = pq.ParquetFile(f).read(columns=["metadata"]).column("metadata").to_pylist()
        for i, meta in enumerate(metas):
            by_category[meta["doc_category"]].append((file, i))

    rng = random.Random(seed)
    categories = sorted(by_category)
    wanted = defaultdict(list)
    for k, cat in enumerate(categories):
        quota = n // len(categories) + (k < n % len(categories))
        for file, i in rng.sample(by_category[cat], min(quota, len(by_category[cat]))):
            wanted[file].append(i)

    records = []
    cols = ["image", "bboxes", "category_id", "pdf_cells", "metadata"]  # skip the heavy `pdf` blob
    for file in src["file"]:
        for i, row in read_parquet_rows(hub_path(src, file), sorted(wanted[file]), cols):
            meta = row["metadata"]
            sid = f"doclaynet_{meta['page_hash'][:16]}"
            layout = [
                {"bbox": bbox, "label": DOCLAYNET_LABELS[cat],
                 "text": " ".join(c["text"] for c in cells if c["text"])}
                for bbox, cat, cells in zip(row["bboxes"], row["category_id"], row["pdf_cells"])
            ]
            records.append({"id": sid, "image": save_image(task_dir, sid, row["image"]["bytes"]),
                            "doc_category": meta["doc_category"], "width": meta["coco_width"],
                            "height": meta["coco_height"], "layout": layout,
                            "metadata": meta, "source_file": file, "source_index": i})
    return records


def build_chart(task_dir: Path, src: dict, n: int, seed: int) -> list[dict]:
    base = "ChartQA Dataset/test"
    with fs.open(hub_path(src)) as f:
        z = zipfile.ZipFile(f)
        names = set(z.namelist())
        qa = defaultdict(list)
        for kind in ("human", "augmented"):
            for item in json.loads(z.read(f"{base}/test_{kind}.json")):
                qa[item["imgname"]].append({"query": item["query"], "label": item["label"], "type": kind})
        # Only charts that have an image, a data table and at least one QA pair.
        charts = sorted(
            name for name in qa
            if f"{base}/png/{name}" in names and f"{base}/tables/{Path(name).stem}.csv" in names
        )
        chosen = [charts[i] for i in sample_indices(len(charts), n, seed)]
        records = []
        for name in chosen:
            stem = Path(name).stem
            sid = f"chartqa_{stem}"
            table_csv = z.read(f"{base}/tables/{stem}.csv").decode("utf-8")
            records.append({
                "id": sid,
                "image": save_image(task_dir, sid, z.read(f"{base}/png/{name}")),
                "table_csv": table_csv,
                "table": list(csv.reader(io.StringIO(table_csv))),
                "qa": qa[name],
                "source_imgname": name,
            })
    return records


BUILDERS = {
    "handwriting": build_handwriting,
    "handwritten_math": build_handwritten_math,
    "chart": build_chart,
    "table": build_table,
    "mixed_document": build_mixed_document,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "benchmark")
    parser.add_argument("--n", type=int, default=200, help="samples per task")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--only", nargs="+", choices=list(BUILDERS), help="build only these tasks")
    parser.add_argument("--force", action="store_true", help="rebuild tasks that already exist")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"tasks": {}}
    manifest.update({"seed": args.seed, "n_per_task": args.n})

    for task in args.only or BUILDERS:
        task_dir = args.out / task
        ann_path = task_dir / "annotations.jsonl"
        prev = manifest["tasks"].get(task, {})
        if ann_path.exists() and not args.force and prev.get("seed") == args.seed and prev.get("n") == args.n:
            print(f"[skip] {task}: already built ({ann_path})")
            continue

        print(f"[build] {task} <- {SOURCES[task]['repo']}")
        shutil.rmtree(task_dir, ignore_errors=True)
        (task_dir / "images").mkdir(parents=True)
        records = BUILDERS[task](task_dir, SOURCES[task], args.n, args.seed)
        with ann_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        manifest["tasks"][task] = {**SOURCES[task], "seed": args.seed, "n": len(records)}
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"[done] {task}: {len(records)} samples")


if __name__ == "__main__":
    main()
