"""Prepare AssetOpsBench static evidence and separate, unscored benchmark questions.

    uv run python -m backend.knowledge_bases.assetopsbench.prepare

Only allowlisted reference files from IBM's companion repository become evidence.
HF scenarios (including grading criteria) are written exclusively to questions.jsonl.
Pinned revisions and source hashes make the conversion reproducible; --offline
rebuilds from the raw cache without a network connection.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from backend.knowledge_bases.assetopsbench import OUTPUT_DIR


HF_REVISION = "5e25bb7f2cd37fb68b9a9e1f99d170ca5be7ce17"
GITHUB_REVISION = "81265cbbd36d7348ec0aeae4b398c86d332f2fc1"
HF_BASE = f"https://huggingface.co/datasets/ibm-research/AssetOpsBench/resolve/{HF_REVISION}"
GITHUB_BASE = f"https://raw.githubusercontent.com/IBM/AssetOpsBench/{GITHUB_REVISION}"
REFERENCE_ROOT = "src/couchdb/scenarios_data/shared"
QUESTION_FILES = {
    "scenarios": "data/scenarios/all_utterance.jsonl",
    "compressor": "data/asset/compressor_utterance.jsonl",
    "hydrolic_pump": "data/asset/hydrolicpump_utterance.jsonl",
    "rule_logic": "data/task/rule_monitoring_scenarios.jsonl",
    "failure_mode_sensor_mapping": "data/task/failure_mapping_senarios.jsonl",
    "prognostics_and_health_management": "data/task/phm_utterance.jsonl",
}


@dataclass(frozen=True)
class ReferenceFile:
    path: str
    source_type: str
    identity_key: str


REFERENCE_FILES = (
    ReferenceFile("iot/asset_profile_sample.json", "asset_profile", "assetnum"),
    ReferenceFile("catalog/assets.csv", "asset_catalog", "asset"),
    ReferenceFile("catalog/sensors.csv", "sensor_catalog", "sensor"),
    ReferenceFile("catalog/failure_modes.csv", "failure_mode_catalog", "failure_mode"),
    ReferenceFile("failure_code/failure_code_sample.csv", "failure_code", "code"),
    ReferenceFile("fmea/failure_modes_sample.json", "failure_modes", "asset_class"),
    ReferenceFile("tsfm/feature_catalog.json", "feature_catalog", "feature_id"),
    ReferenceFile("tsfm/model_catalog.json", "model_catalog", "model_id"),
    ReferenceFile("work_order/workorders.csv", "work_order", "wonum"),
)
CORPUS_COLUMNS = (
    "doc_id", "source_type", "title", "asset_id", "asset_type", "site_id",
    "source_path", "source_url", "source_revision", "document",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reference_rows(source: ReferenceFile, raw: bytes) -> list[dict[str, str]]:
    """Keep each source object intact; never derive evidence from a question."""
    text = raw.decode("utf-8-sig")
    records = (
        json.loads(text) if source.path.endswith(".json")
        else list(csv.DictReader(io.StringIO(text, newline="")))
    )
    if not isinstance(records, list) or not records:
        raise ValueError(f"{source.path}: expected a nonempty list of source records")
    rows = []
    seen = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or not record:
            raise ValueError(f"{source.path}:{index}: expected an object")
        if source.path.endswith(".csv") and (None in record or None in record.values()):
            raise ValueError(f"{source.path}:{index}: malformed CSV row")
        identity = record.get(source.identity_key)
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"{source.path}:{index}: missing {source.identity_key}")
        # Site is part of an asset/work-order identity; spelling is never normalized.
        key = json.dumps([source.path, record.get("siteid"), identity], ensure_ascii=False)
        doc_id = "assetopsbench_" + sha256(key.encode("utf-8"))[:24]
        if doc_id in seen:
            raise ValueError(f"{source.path}:{index}: duplicate identity {identity!r}")
        seen.add(doc_id)
        title = f"{source.source_type}: {identity}"
        source_path = f"{REFERENCE_ROOT}/{source.path}"
        rows.append({
            "doc_id": doc_id,
            "source_type": source.source_type,
            "title": title,
            "asset_id": record.get("assetnum") or "",
            "asset_type": record.get("assettype") or record.get("asset_class") or "",
            "site_id": record.get("siteid") or "",
            "source_path": source_path,
            "source_url": f"{GITHUB_BASE}/{source_path}",
            "source_revision": GITHUB_REVISION,
            "document": (
                f"{title}\nSource: IBM AssetOpsBench public static sample\n"
                "Scope: this record is sample reference data, not an exhaustive inventory "
                "or a live system observation. Empty/null fields are unprovided.\n"
                f"Source path: {source_path}\nRecord:\n"
                + json.dumps(record, ensure_ascii=False, indent=2)
            ),
        })
    return rows


def question_rows(config: str, raw: bytes) -> list[dict]:
    rows = []
    seen = set()
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{config}:{line_number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{config}:{line_number}: expected an object")
        original_id, question = record.get("id"), record.get("text")
        if (
            isinstance(original_id, bool) or not isinstance(original_id, (str, int))
            or not str(original_id).strip() or not isinstance(question, str) or not question.strip()
        ):
            raise ValueError(f"{config}:{line_number}: expected id and nonempty text")
        question_id = f"{config}:{original_id}"
        if question_id in seen:
            raise ValueError(f"{config}:{line_number}: duplicate id {original_id!r}")
        seen.add(question_id)
        rows.append({
            "question_id": question_id,
            "question": question,
            "config": config,
            "split": "train",
            "source_url": f"{HF_BASE}/{QUESTION_FILES[config]}",
            "source_revision": HF_REVISION,
            # Preserve every original annotation for later evaluation, outside the KB.
            "source_record": record,
        })
    if not rows:
        raise ValueError(f"{config}: no questions")
    return rows


def fetch_source(
    client: httpx.Client, output_dir: Path, origin: str, revision: str,
    path: str, url: str, *, offline: bool, force: bool,
) -> tuple[bytes, dict]:
    cache = output_dir / "raw" / origin / revision / path
    if force or not cache.is_file():
        if offline:
            raise ValueError(f"Offline source missing: {cache}; run prepare online first")
        response = client.get(url)
        response.raise_for_status()
        raw = response.content
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(cache.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(cache)
    else:
        raw = cache.read_bytes()
    return raw, {
        "origin": origin, "revision": revision, "path": path, "url": url,
        "sha256": sha256(raw), "bytes": len(raw),
    }


def prepare(
    output_dir: Path = OUTPUT_DIR, *, offline: bool = False, force: bool = False,
    client: httpx.Client | None = None,
) -> dict:
    if offline and force:
        raise ValueError("--offline and --force cannot be combined")
    if client is None:
        with httpx.Client(follow_redirects=True, timeout=120) as connection:
            return prepare(output_dir, offline=offline, force=force, client=connection)

    corpus, questions, sources = [], [], []
    for source in REFERENCE_FILES:
        path = f"{REFERENCE_ROOT}/{source.path}"
        raw, metadata = fetch_source(
            client, output_dir, "github", GITHUB_REVISION, path,
            f"{GITHUB_BASE}/{path}", offline=offline, force=force,
        )
        corpus.extend(reference_rows(source, raw))
        sources.append(metadata)
    for config, path in QUESTION_FILES.items():
        raw, metadata = fetch_source(
            client, output_dir, "huggingface", HF_REVISION, path,
            f"{HF_BASE}/{path}", offline=offline, force=force,
        )
        questions.extend(question_rows(config, raw))
        sources.append(metadata)

    manifest = {
        "adapter_version": "static-reference-v1",
        "scope": "IBM companion repository static sample; not the complete AssetOpsBench environment",
        "questions_in_corpus": False,
        "excluded": ["grading criteria and question text", "FailureSensorIQ QA variants",
                     "sensor time series", "live tools and model execution"],
        "evaluation": "Questions are unscored; corpus coverage and gold document IDs are not established",
        "corpus_rows": len(corpus),
        "corpus_by_source_type": dict(sorted(Counter(r["source_type"] for r in corpus).items())),
        "question_rows": len(questions),
        "questions_by_config": dict(sorted(Counter(q["config"] for q in questions).items())),
        "sources": sources,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    # Validate every source before replacing output. Manifest is published last.
    with TemporaryDirectory(prefix=".prepare-", dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / "corpus.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CORPUS_COLUMNS)
            writer.writeheader()
            writer.writerows(corpus)
        with (staging / "questions.jsonl").open("w", encoding="utf-8") as handle:
            for question in questions:
                handle.write(json.dumps(question, ensure_ascii=False) + "\n")
        manifest["outputs"] = {
            name: {"sha256": sha256((staging / name).read_bytes())}
            for name in ("corpus.csv", "questions.jsonl")
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        for name in ("corpus.csv", "questions.jsonl", "manifest.json"):
            (staging / name).replace(output_dir / name)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Rebuild using only cached raw sources")
    mode.add_argument("--force", action="store_true", help="Download pinned sources again")
    args = parser.parse_args()
    try:
        manifest = prepare(args.output_dir, offline=args.offline, force=args.force)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")
    print(f"{args.output_dir / 'corpus.csv'}: {manifest['corpus_rows']} static reference documents")
    print(f"{args.output_dir / 'questions.jsonl'}: {manifest['question_rows']} questions (not ingested)")
    print("Static sample only; benchmark coverage and gold document mappings are not established.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
