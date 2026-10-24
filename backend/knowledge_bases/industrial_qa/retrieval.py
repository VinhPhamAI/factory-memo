"""Industrial QA scoped retrieval and reranking over stores; no benchmark files or run directories."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
import json
import re
from typing import Any

from pydantic import BaseModel

from backend.knowledge_bases.industrial_qa import CATALOG
from backend.knowledge_bases.industrial_qa.prompts import fill_prompt
from backend.knowledge_bases.industrial_qa.query_rewrite import QUERY_REWRITE_MODES, QueryPlan
from backend.knowledge_bases.industrial_qa.scope import detected_scope, inherit_follow_up_scope, manual_query
from backend.src.agents.rag.store import ChunkHit
from backend.src.services import llm
from backend.src.storage import KnowledgeRecord
from backend.src.storage.knowledge_store import build_knowledge_filter


DOCUMENT_QUERY_INSTRUCTION = (
    "Given a question from a factory maintenance engineer, retrieve the equipment manual passage that answers it"
)
UNSCOPED_CONTEXT = frozenset({"case_card", "steps", "source_turns", "benchmark_run_id"})
# Both modes use the LLM reranker. full keeps the scoped candidate pool; top_k
# cuts it in hybrid-search order per source before adding procedure neighbours.
RERANK_INPUT_MODES = ("full", "top_k")
# A record's subject and claim, cut to this length, form one second-hop manual query.
SECOND_HOP_QUERY_CHARACTERS = 600


@dataclass(frozen=True)
class RetrievalConfig:
    k: int = 8
    per_conversation: int = 3
    context_characters: int = 24000
    knowledge_candidates: int = 20
    manual_candidates: int = 48
    manual_rerank_candidates: int = 16
    rerank_input_mode: str = "top_k"
    # Kept per source by the top-k input cut or the optional cross-encoder prefilter.
    prefilter_knowledge: int = 8
    prefilter_manual: int = 8
    # Cosine similarity that marks a knowledge hit confident (confident hits rank
    # first); None keeps the store's KNOWLEDGE_MIN_VECTOR_SIMILARITY.
    min_vector_similarity: float | None = None
    # Second-hop manual search from the top knowledge records; 0 disables it.
    second_hop_records: int = 0
    second_hop_per_record: int = 4
    second_hop_manual: int = 4
    # Query rewriting (query_rewrite.py): off, auto (follow-ups, non-English) or always.
    query_rewrite: str = "off"

    def __post_init__(self):
        if self.rerank_input_mode not in RERANK_INPUT_MODES:
            raise ValueError(f"Unknown industrial QA rerank input mode: {self.rerank_input_mode}")
        if self.query_rewrite not in QUERY_REWRITE_MODES:
            raise ValueError(f"Unknown industrial QA query_rewrite mode: {self.query_rewrite}")
        if min(self.k, self.context_characters, self.knowledge_candidates,
               self.manual_candidates, self.manual_rerank_candidates,
               self.prefilter_knowledge, self.prefilter_manual,
               self.second_hop_per_record, self.second_hop_manual) < 1 or min(
               self.per_conversation, self.second_hop_records) < 0:
            raise ValueError("Invalid industrial QA retrieval limits")
        if self.min_vector_similarity is not None and not 0 <= self.min_vector_similarity <= 1:
            raise ValueError("min_vector_similarity must be between 0 and 1")


def interleaved(first, second, key, limit):
    """Alternate two rankings, best ranks first, without duplicates."""
    merged = {}
    for pair in zip(first, second):
        for item in pair:
            merged.setdefault(key(item), item)
    longer = first if len(first) > len(second) else second
    for item in longer[min(len(first), len(second)):]:
        merged.setdefault(key(item), item)
    return list(merged.values())[:limit]


def render_record(label, record):
    scope = {key: value for key, value in record.context.items()
             if key not in UNSCOPED_CONTEXT and value not in (None, "", [])}
    head = f"[{label}] {record.type.value}: {record.subject}"
    if scope:
        head += " " + json.dumps(scope, ensure_ascii=False)
    return f"{head}\n{record.claim}"


def reference_id(passage):
    return (passage.metadata.get("reference_doc_id") or passage.metadata.get("doc_id")
            or re.sub(r"_p\d+_c\d+$", "", passage.doc_id))


def page_start(passage):
    if passage.metadata.get("page_start") is not None:
        return int(passage.metadata["page_start"])
    match = re.search(r"_p(\d+)_c\d+$", passage.doc_id)
    return int(match[1]) if match else None


def render_passage(label, passage):
    meta = passage.metadata
    page = page_start(passage)
    pages = f", p. {page}" if page is not None else ""
    if page is not None and meta.get("page_end") not in (None, page):
        pages += f"-{meta['page_end']}"
    return f"[{label}] manual {reference_id(passage)}{pages}, {passage.title}\n{passage.text}"


@dataclass(frozen=True)
class Source:
    label: str
    record: KnowledgeRecord | None = None
    passage: ChunkHit | None = None
    candidate_label: str = ""
    confident: bool | None = None

    def __post_init__(self):
        if (self.record is None) == (self.passage is None):
            raise ValueError("A source must have exactly one record or passage")

    @property
    def text(self):
        return render_record(self.label, self.record) if self.record else render_passage(self.label, self.passage)

    @property
    def kind(self):
        return "knowledge" if self.record else "document"


@dataclass(frozen=True)
class RetrievalResult:
    sources: tuple[Source, ...]
    assets: tuple[str, ...] = ()
    family: str | None = None
    # Candidates the reranker saw, and how many there were before the optional prefilter.
    candidate_count: int = 0
    candidate_pool: int = 0
    query_plan: QueryPlan | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class CandidateSet:
    """Scoped candidates from both stores, labelled K1.. and D1.., before any reranking."""
    assets: tuple[str, ...]
    family: str | None
    sources: dict[str, Source]
    plan: QueryPlan


class Ranking(BaseModel):
    labels: list[str]


PROCEDURE_RE = re.compile(r"procedure|steps?|connect|configur", re.I)
# Only the top manual passages seed neighbour expansion.
PROCEDURE_SEEDS = 3
NEIGHBOUR_PAGES = 2


def similarity_threshold(cfg):
    return {} if cfg.min_vector_similarity is None else {"min_vector_similarity": cfg.min_vector_similarity}


def capped_per_conversation(hits, limit):
    """At most `limit` hits from each source conversation, in rank order."""
    counts = defaultdict(int)
    kept = []
    for hit in hits:
        key = str(hit.record.evidence_id)
        if counts[key] < limit:
            kept.append(hit)
            counts[key] += 1
    return kept


def deduplicated(passages):
    return list({p.chunk_id: p for p in passages}.values())


def same_section(neighbour, seed):
    """Same manual, same top-level section and within NEIGHBOUR_PAGES pages."""
    return (reference_id(neighbour) == reference_id(seed)
            and neighbour.title.split(" > ")[0] == seed.title.split(" > ")[0]
            and (page_start(seed) is None or page_start(neighbour) is None
                 or abs(page_start(seed) - page_start(neighbour)) <= NEIGHBOUR_PAGES))


async def procedure_neighbours(passages, documents):
    """Adjacent chunks of the top procedure passages, so multi-chunk procedures stay whole."""
    seeds = [p for p in passages[:PROCEDURE_SEEDS] if PROCEDURE_RE.search(p.text)]
    if not seeds:
        return []
    neighbours = await documents.adjacent_chunks([p.chunk_id for p in seeds])
    return [n for n in neighbours if any(same_section(n, seed) for seed in seeds)]


def labelled(hits, passages):
    sources = {f"K{i}": Source(f"K{i}", record=hit.record, confident=hit.confident) for i, hit in enumerate(hits, 1)}
    sources.update({f"D{i}": Source(f"D{i}", passage=p) for i, p in enumerate(passages, 1)})
    return sources


class IndustrialRetriever:
    def __init__(self, ranker, *, config=RetrievalConfig(), prompt: str, prefilter=None, env_prefix="ANSWER",
                 rewriter=None):
        self.ranker, self.config, self.prompt, self.prefilter = ranker, config, prompt, prefilter
        self.env_prefix, self.rewriter = env_prefix, rewriter

    async def plan(self, question, history, cfg):
        if cfg.query_rewrite == "off":
            return QueryPlan(question, inherit_follow_up_scope(question, history))
        if self.rewriter is None:
            raise ValueError("query_rewrite needs a query rewriter")
        return await self.rewriter.plan(question, history, mode=cfg.query_rewrite)

    async def candidates(self, question, knowledge, documents, *, scope=None, config=None, history=()):
        """Scoped hybrid candidates from both stores, before any reranking.
        `question` is the user's own words; `history` holds prior (question,
        answer) turns of an API thread."""
        cfg = config if config is not None else self.config
        # The original branch: a follow-up without scope inherits the latest scoped prior question.
        base = inherit_follow_up_scope(question, history)
        assets, family = detected_scope(base)
        local = CATALOG.lookup_scope(assets, family)
        filters = {"$and": [scope, local]} if scope and local else scope or local
        hits, passages, plan = await self.search(question, history, base, family, filters, knowledge, documents, cfg)
        hits = await self.knowledge_candidates(hits, base, assets, family, scope, knowledge, cfg)
        passages = await self.manual_candidates(passages, hits, family, documents, cfg)
        return CandidateSet(tuple(assets), family, labelled(hits, passages), plan)

    async def search(self, question, history, base, family, filters, knowledge, documents, cfg):
        """Hybrid search of both stores with the original branch; a usable
        rewrite only adds candidates, alternating with the original ranks."""
        threshold = similarity_threshold(cfg)

        def search_knowledge(text):
            return knowledge.search_knowledge(text, k=cfg.knowledge_candidates, filters=filters, **threshold)

        def search_manuals(text):
            return documents.search(manual_query(text, family), k=cfg.manual_candidates)

        hits, passages, plan = await asyncio.gather(
            search_knowledge(base), search_manuals(base), self.plan(question, history, cfg))
        if plan.rewritten is not None:
            extra_hits, extra_passages = await asyncio.gather(
                search_knowledge(plan.rewritten.knowledge_query), search_manuals(plan.rewritten.manual_query))
            hits = interleaved(hits, extra_hits, lambda hit: hit.record.id, cfg.knowledge_candidates)
            passages = interleaved(passages, extra_passages, lambda p: p.chunk_id, cfg.manual_candidates)
        return hits, passages, plan

    async def knowledge_candidates(self, hits, base, assets, family, scope, knowledge, cfg):
        """Without a named asset, cap hits per conversation; when a named asset
        matches nothing, fall back to general and family-compatible records."""
        if not assets and cfg.per_conversation:
            hits = capped_per_conversation(hits, cfg.per_conversation)
        elif assets and not hits:
            general = {"context.machine_id": {"$exists": False}}
            compatible = build_knowledge_filter(context={"machine_model": family})
            parts = [part for part in (scope, general, compatible) if part]
            hits = await knowledge.search_knowledge(base, k=cfg.knowledge_candidates, filters={"$and": parts},
                                                    **similarity_threshold(cfg))
        return hits[:cfg.prefilter_knowledge] if cfg.rerank_input_mode == "top_k" else hits

    async def manual_candidates(self, passages, hits, family, documents, cfg):
        """Passages of the family's own manual (all passages when it has none),
        cut to the rerank budget, plus second-hop passages and procedure neighbours."""
        expected_manual = CATALOG.families[family].manual if family else None
        allowed = [p for p in passages if not expected_manual or reference_id(p) == expected_manual] or passages
        # The top-k input cut precedes neighbour expansion so multi-chunk procedures stay whole.
        limit = cfg.manual_rerank_candidates
        if cfg.rerank_input_mode == "top_k":
            limit = min(limit, cfg.prefilter_manual)
        selected = deduplicated(allowed)[:limit]
        if cfg.second_hop_records and hits:
            selected += await self.second_hop([hit.record for hit in hits[:cfg.second_hop_records]],
                family, expected_manual, {p.chunk_id for p in selected}, documents, cfg)
        selected += await procedure_neighbours(selected, documents)
        return deduplicated(selected)

    async def second_hop(self, records, family, manual, seen, documents, cfg):
        """Manual passages found with the top records' wording, which bridges a
        symptom-style question to manual vocabulary (e.g. a station number
        parameter). Records only steer the search; the reranker and writer
        still judge every passage against the original question."""
        queries = [manual_query(f"{r.subject}: {r.claim}"[:SECOND_HOP_QUERY_CHARACTERS], family) for r in records]
        results = await asyncio.gather(*(documents.search(q, k=cfg.second_hop_per_record) for q in queries))
        found = {}
        # Interleave by rank so the first record cannot fill every slot.
        for rank in range(cfg.second_hop_per_record):
            for passages in results:
                if rank < len(passages):
                    p = passages[rank]
                    if p.chunk_id not in seen and (not manual or reference_id(p) == manual):
                        found.setdefault(p.chunk_id, p)
        return list(found.values())[:cfg.second_hop_manual]

    async def prefiltered(self, question, candidates, cfg):
        """Cross-encoder cut before the LLM. Quotas are per source because
        scores of short records and long manual chunks are not comparable."""
        items = list(candidates.values())
        scores = await self.prefilter.score(question, [re.sub(r"^\[[KD]\d+\] ", "", s.text) for s in items])
        kept = {}
        for kind, limit in (("K", cfg.prefilter_knowledge), ("D", cfg.prefilter_manual)):
            ranked = sorted((pair for pair in zip(scores, items) if pair[1].label[0] == kind),
                            key=lambda pair: -pair[0])[:limit]
            kept.update({f"{kind}{i}": Source(f"{kind}{i}", s.record, s.passage, confident=s.confident)
                         for i, (_, s) in enumerate(ranked, 1)})
        return kept

    async def retrieve(self, question, knowledge, documents, *, scope=None, context_characters=None, config=None,
                       history=()):
        cfg = config if config is not None else self.config
        found = await self.candidates(question, knowledge, documents, scope=scope, config=cfg, history=history)
        candidates, plan = found.sources, found.plan
        pool = len(candidates)
        question = plan.rerank_question
        if self.prefilter is not None and candidates:
            candidates = await self.prefiltered(question, candidates, cfg)
        if not candidates:
            return RetrievalResult((), found.assets, found.family, query_plan=plan)
        response = await llm.ainvoke(self.ranker, fill_prompt(
            self.prompt, QUESTION=question, SOURCES="\n\n".join(s.text for s in candidates.values())))
        ranking = llm.validated(Ranking, llm.response_json(response, task="source ranking", env_prefix=self.env_prefix), task="source ranking")
        if set(ranking.labels) - candidates.keys():
            raise llm.LLMResponseError("Reranker returned unknown source labels")
        counts, used, sources = {"K": 0, "D": 0}, 0, []
        budget = cfg.context_characters if context_characters is None else context_characters
        for label in dict.fromkeys(ranking.labels):
            item = candidates[label]
            kind, size = label[0], len(item.text)
            if counts[kind] >= cfg.k or used + size > budget:
                continue
            counts[kind] += 1
            sources.append(Source(f"{kind}{counts[kind]}", item.record, item.passage, label, item.confident))
            used += size
        if not sources:
            raise llm.LLMResponseError("Reranker selected no evidence within context budget")
        return RetrievalResult(tuple(sources), found.assets, found.family, candidate_count=len(candidates),
                               candidate_pool=pool, query_plan=plan, usage=getattr(response, "usage_metadata", None))
