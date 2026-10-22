"""Run manifests: the code, prompts, inputs and models a run was produced with.

A resumed run must produce the same manifest; any change needs a new run
directory. Paths are recorded relative to the project root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from backend.knowledge_bases.base import PROJECT_ROOT
from backend.knowledge_bases.industrial_qa.benchmark.data import GOLD_DIR, PUBLIC_DIR, save
from backend.knowledge_bases.industrial_qa.config import sha


PACKAGE_DIR = Path(__file__).resolve().parents[1]
# Relative to the industrial_qa package. The extraction contract decides
# whether a run may reuse older extractions.
EXTRACTION_SOURCES = ("schema.py", "extract.md", "catalog_types.yaml", "catalog_assets.yaml", "ontology.yaml")
PIPELINE_SOURCES = ("catalog.py", "scope.py", "prompts.py", "query_rewrite.py", "retrieval.py", "answering.py",
                    "pipeline.py", "config.py", "benchmark/adapter.py", "benchmark/evidence.py",
                    "rerank.md", "query.md", "answer.md")
# Benchmark code shared by the runners that ingest, answer and evaluate.
RUNNER_SOURCES = ("benchmark/data.py", "benchmark/runs.py", "benchmark/manifest.py", "benchmark/ingest.py",
                  "benchmark/predictions.py", "benchmark/evaluation.py")
MODEL_SETTINGS = ("OPENROUTER_MODEL", "EXTRACTION_MODEL", "ANSWER_MODEL", "EMBEDDING_MODEL", "EMBEDDING_DIM",
                  "ANSWER_THINKING", "EXTRACTION_THINKING", "MODELARK_MODELNAME")


def source_paths(*names):
    return [PACKAGE_DIR / name for name in names]


def project_path(path):
    return str(path.resolve().relative_to(PROJECT_ROOT))


def file_hashes(paths):
    """SHA-256 of each file's text, keyed by its project-relative path."""
    return {project_path(path): sha(path.read_text()) for path in paths}


def split_inputs(split):
    """The benchmark files a run of the split reads (gold only to score it)."""
    return [PUBLIC_DIR / "conversations.jsonl", PUBLIC_DIR / "corpus_chunks.jsonl",
            PUBLIC_DIR / f"questions_{split}.jsonl", GOLD_DIR / f"{split}.jsonl"]


def models():
    return {key: os.getenv(key) for key in MODEL_SETTINGS}


def write_manifest(run_dir, manifest, *, sources=(), config_path=None,
                   changed="Configuration changed; preserve this run and use a new run directory"):
    """Save manifest.json, refusing a resume whose manifest differs, and copy
    the config and sources into <run_dir>/snapshot (package-relative paths)."""
    path = run_dir / "manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError(changed)
    snapshot = run_dir / "snapshot"
    if config_path is not None:
        snapshot.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config_path, snapshot / f"config_{config_path.name}")
    for source in sources:
        target = snapshot / source.relative_to(PACKAGE_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    save(path, manifest)
