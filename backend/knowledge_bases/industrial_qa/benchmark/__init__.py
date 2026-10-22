"""Benchmark runners for the industrial QA knowledge base.

They evaluate the official pipeline (pipeline.py) on benchmark/ and never feed
gold labels to it. Modules:

- data: benchmark files, transcripts and manual chunks.
- runs: run checkpoints and the single-database write policy.
- manifest: source/input hashes and the per-run snapshot of code and config.
- ingest: manual indexing and knowledge extraction into a run.
- renormalize: rescoping stored records with the current catalog and ontology.
- adapter: the official retrieval and answer pipeline wrapped for predictions.
- evidence: mapping retrieved sources to benchmark evidence items.
- predictions: answering a run's questions into predictions.jsonl.
- evaluation: scoring with benchmark/evaluate.py plus ingestion metrics.
- pilot, train, holdout, reanswer: the runnable entry points (python -m ...).
- runner: the steps train and holdout share.
"""
