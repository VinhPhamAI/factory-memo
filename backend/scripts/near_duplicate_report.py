"""Read-only report for tuning the near-duplicate thresholds on a KB's real data.

Examples:
    uv run python -m backend.scripts.near_duplicate_report --kb general
    uv run python -m backend.scripts.near_duplicate_report --kb general --mode knowledge --top 30
    uv run python -m backend.scripts.near_duplicate_report --kb general --jaccard 0.85 --containment 0.8
    uv run python -m backend.scripts.near_duplicate_report --kb general --all-sources

Loads the evidence texts of one KB (by default those with a done or running
extraction run, per mode, as the warning compares them), scores every pair
that shares a shingle exactly, and prints the score distribution and the top
pairs with the level each would get. In each pair the newer source is the one
being checked, so containment is "share of the newer text found in the older
one". The `lsh` column says whether the stored LSH bands would have proposed
the pair as a candidate (pairs it misses are never warned about).

Nothing is written: the KB database must exist and is read in a read-only
transaction. Source names and IDs are printed, transcripts are not.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.knowledge_bases.base import DATABASE_PREFIX
from backend.src.storage.content_signatures import (
    CONFIRM_LEVELS,
    Overlap,
    Thresholds,
    signature,
)
from backend.src.storage.databases import database_exists, database_url, server_url_from_env
from backend.src.storage.extraction_runs import content_sha256


BINS = 10
# Identical content is blocked by its SHA-256, not by this warning.
EXACT = "exact"


@dataclass(frozen=True)
class Source:
    evidence_id: str
    name: str
    mode: str
    created_at: datetime
    text: str


@dataclass(frozen=True)
class Pair:
    newer: Source
    older: Source
    scores: Overlap
    level: str | None
    lsh: bool

    @property
    def top(self) -> float:
        return max(self.scores.jaccard, self.scores.containment, self.scores.coverage)


def score_pairs(sources: Sequence[Source], thresholds: Thresholds) -> tuple[list[Pair], int]:
    """Exact scores of every same-mode pair sharing a shingle; also the number of pairs compared."""

    signed = [signature(source.text) for source in sources]
    hashes = [content_sha256(source.text) for source in sources]
    pairs, compared = [], 0
    by_mode: dict[str, list[int]] = defaultdict(list)
    for index, source in enumerate(sources):
        by_mode[source.mode].append(index)
    for indexes in by_mode.values():
        compared += len(indexes) * (len(indexes) - 1) // 2
        # An inverted index counts shared shingles without comparing disjoint pairs.
        postings: dict[str, list[int]] = defaultdict(list)
        for index in indexes:
            for item in signed[index].shingles:
                postings[item].append(index)
        shared: Counter[tuple[int, int]] = Counter()
        for members in postings.values():
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    shared[(a, b)] += 1
        for (a, b), count in shared.items():
            # The newer source is the one checked against the older at extraction.
            new, old = (a, b) if (sources[a].created_at, a) >= (sources[b].created_at, b) else (b, a)
            new_items, old_items = signed[new].shingles, signed[old].shingles
            scores = Overlap(count / (len(new_items) + len(old_items) - count),
                             count / len(new_items), count / len(old_items))
            level = EXACT if hashes[new] == hashes[old] else thresholds.level(scores)
            pairs.append(Pair(sources[new], sources[old], scores, level,
                              bool(set(signed[new].bands) & set(signed[old].bands))))
    pairs.sort(key=lambda pair: (-pair.top, -pair.scores.jaccard))
    return pairs, compared


def histogram(values: Iterable[float]) -> list[int]:
    counts = [0] * BINS
    for value in values:
        counts[min(int(value * BINS), BINS - 1)] += 1
    return counts


def format_report(kb: str, sources: Sequence[Source], pairs: Sequence[Pair], compared: int,
                  thresholds: Thresholds, top: int) -> str:
    lines = [f"Knowledge base {kb}: thresholds {thresholds.to_json()}"]
    modes = Counter(source.mode for source in sources)
    lines.append("Sources: " + (", ".join(f"{mode} {count}" for mode, count in sorted(modes.items())) or "none"))
    lines.append(f"Same-mode pairs: {compared}; sharing a shingle: {len(pairs)}; "
                 f"without any shared shingle: {compared - len(pairs)}")
    levels = Counter(pair.level for pair in pairs if pair.level)
    lines.append("Levels: " + ", ".join(f"{level} {levels.get(level, 0)}"
                                        for level in (EXACT, *CONFIRM_LEVELS, "similar")))
    missed = [pair for pair in pairs if pair.level not in (None, EXACT) and not pair.lsh]
    lines.append(f"Flagged pairs LSH would miss (no warning in the app): {len(missed)}")
    if not pairs:
        return "\n".join(lines)

    lines += ["", "Pairs sharing a shingle, by score range:",
              f"{'range':<11}{'jaccard':>9}{'contain':>9}{'coverage':>10}"]
    columns = [histogram(pair.scores.jaccard for pair in pairs),
               histogram(pair.scores.containment for pair in pairs),
               histogram(pair.scores.coverage for pair in pairs)]
    for bin_ in reversed(range(BINS)):
        label = f"{bin_ / BINS:.1f}-{(bin_ + 1) / BINS:.1f}"
        lines.append(f"{label:<11}{columns[0][bin_]:>9}{columns[1][bin_]:>9}{columns[2][bin_]:>10}")

    lines += ["", f"Top {min(top, len(pairs))} pairs (newer -> older; containment = newer inside older):",
              f"{'jaccard':>8}{'contain':>8}{'cover':>7}  {'level':<15}{'lsh':<5}{'mode':<10}newer -> older"]
    for pair in pairs[:top]:
        lines.append(
            f"{pair.scores.jaccard:>8.3f}{pair.scores.containment:>8.3f}{pair.scores.coverage:>7.3f}  "
            f"{pair.level or '-':<15}{'yes' if pair.lsh else 'no':<5}{pair.newer.mode:<10}"
            f"{label_of(pair.newer)} -> {label_of(pair.older)}"
        )
    return "\n".join(lines)


def label_of(source: Source) -> str:
    name = source.name if len(source.name) <= 40 else source.name[:39] + "…"
    return f"«{name}» [{source.evidence_id[:8]}]"


async def fetch_sources(conn, *, mode: str | None, all_sources: bool, limit: int) -> list[Source]:
    """Evidence texts to compare, from the connection's KB tables (unqualified, so the search path decides)."""

    has_runs = (await conn.execute(text("SELECT to_regclass('extraction_runs') IS NOT NULL"))).scalar()
    if all_sources or not has_runs:
        if not all_sources:
            print("No extraction_runs table yet: comparing all evidence rows.", file=sys.stderr)
        query = ("SELECT id, metadata, created_at, transcript, 'all' AS mode FROM raw_conversations "
                 "ORDER BY created_at DESC LIMIT :limit")
        parameters: dict = {"limit": limit}
    else:
        # One row per source and mode with a done or running run, as the app compares them.
        query = ("SELECT * FROM (SELECT DISTINCT ON (c.id, r.mode) c.id, c.metadata, c.created_at, "
                 "c.transcript, r.mode FROM extraction_runs r JOIN raw_conversations c ON c.id = r.evidence_id "
                 "WHERE r.status IN ('done', 'running') AND (CAST(:mode AS text) IS NULL OR r.mode = :mode) "
                 "ORDER BY c.id, r.mode) extracted ORDER BY created_at DESC LIMIT :limit")
        parameters = {"mode": mode, "limit": limit}
    rows = (await conn.execute(text(query), parameters)).mappings().all()
    return [Source(str(row["id"]), (row["metadata"] or {}).get("name") or (row["metadata"] or {}).get("filename")
                   or str(row["id"]), row["mode"], row["created_at"], row["transcript"] or "")
            for row in rows]


async def load_sources(kb: str, *, mode: str | None, all_sources: bool, limit: int) -> list[Source]:
    server = server_url_from_env()
    database = f"{DATABASE_PREFIX}{kb}"
    if not await database_exists(server, database):
        raise SystemExit(f"Database {database} does not exist; nothing to report.")
    engine = create_async_engine(database_url(server, database))
    try:
        async with engine.connect() as conn:
            # Must be the first statement of the transaction: nothing can be written.
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            sources = await fetch_sources(conn, mode=mode, all_sources=all_sources, limit=limit)
            await conn.rollback()
    finally:
        await engine.dispose()
    return sources


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kb", required=True, help="Knowledge base name (database kb_<name>).")
    parser.add_argument("--mode", choices=["knowledge", "documents"], help="Only sources extracted in this mode.")
    parser.add_argument("--all-sources", action="store_true",
                        help="Compare every evidence row, extracted or not, as one group.")
    parser.add_argument("--top", type=int, default=20, help="Pairs to list (default 20).")
    parser.add_argument("--limit", type=int, default=2000, help="Most recent sources to load (default 2000).")
    parser.add_argument("--jaccard", type=float, help="Override NEAR_DUPLICATE_JACCARD.")
    parser.add_argument("--containment", type=float, help="Override NEAR_DUPLICATE_CONTAINMENT.")
    parser.add_argument("--info", type=float, help="Override NEAR_DUPLICATE_INFO.")
    args = parser.parse_args(argv)
    load_dotenv()
    current = Thresholds.from_env()
    thresholds = Thresholds(
        jaccard=current.jaccard if args.jaccard is None else args.jaccard,
        containment=current.containment if args.containment is None else args.containment,
        info=current.info if args.info is None else args.info,
    )
    sources = asyncio.run(load_sources(args.kb, mode=args.mode, all_sources=args.all_sources, limit=args.limit))
    pairs, compared = score_pairs(sources, thresholds)
    print(format_report(args.kb, sources, pairs, compared, thresholds, args.top))


if __name__ == "__main__":
    main()
