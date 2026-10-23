"""Find stored knowledge that a source's newly extracted records may contradict.

Runs as the `conflict` job stage after Extract, once per evidence source:

- L0/L1: only searchable records of the same isolation scope and another
  source, whose validity overlaps and whose hard scope keys (machine model,
  product, configuration, ...) are equal or unset on one side; cases are
  compared with cases only when `compare_cases` is on.
- L2: the nearest records by vector, by full-text search on the subject and
  anchors (parameter codes, alarms, values with units), records sharing an
  anchor first, then soft keys (condition, failure mode); at most K per new
  record and `max_pairs_per_source` per source.
- L3: one listwise LLM call per new record judges its candidates (`list`),
  or one call per pair (`pair`, the evaluation baseline).
- L4: a conflicting listwise verdict is confirmed by one pairwise call.

Every judged pair is stored in `knowledge_conflicts` (cache, review queue and
evaluation data). Only a conflict confirmed by both steps, with enough
confidence and verbatim quotes from the right records, is linked on the
records (`conflict_with`, or `contradicted_by` for a lesson contradicted by
a case); both stay drafts. A judgement is never a verdict on engineering
correctness.

A reviewer's decision holds until either record's content changes. Then the
pair is judged again, even outside the top K, and links of an earlier
judgement that no longer holds are withdrawn (with a history event).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.src.services import llm
from backend.src.services.knowledge_consolidation import DEFAULT_ISOLATION_KEY, LESSON_TYPES
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage.knowledge_conflicts import MACHINE_STATUSES, ConflictRow, ordered
from backend.src.storage.models import KnowledgeLinkUpdate, KnowledgeRecord, KnowledgeStatus


JUDGE_MODES = ("list", "pair")
MAX_CANDIDATES = 8
CONFLICT_MAX_TOKENS = 4096
RRF_RANK_CONSTANT = 60
# Quotes shorter than this (after whitespace normalization) prove nothing.
MIN_QUOTE_CHARACTERS = 8
MAX_EVIDENCE_QUOTES = 3
MAX_QUOTE_CHARACTERS = 300
# Context keys that are not scope: rendered elsewhere or never shown.
_UNSCOPED = frozenset({"case_card", "quotes", "steps", "source_turns"})


class ConflictRequestError(RuntimeError):
    """The LLM endpoint could not be reached; the job is retried with backoff."""


class Relation(str, Enum):
    CONTRADICTION = "contradiction"
    CASE_EXCEPTION = "case_exception"
    SCOPE_DIFFERENCE = "scope_difference"
    REFINEMENT = "refinement"
    EQUIVALENT = "equivalent"
    UNRELATED = "unrelated"


CONFLICTING = frozenset({Relation.CONTRADICTION, Relation.CASE_EXCEPTION})


class Aspect(str, Enum):
    VALUE = "value"
    PROCEDURE_ORDER = "procedure_order"
    CAUSE = "cause"
    ACTION = "action"
    APPLICABILITY = "applicability"
    OTHER = "other"


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str
    relation: Relation
    aspect: Aspect = Aspect.OTHER
    # The scope both records speak about.
    overlap: str = ""
    quote_new: str = ""
    quote_old: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class Verdicts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[Verdict] = Field(default_factory=list)


@dataclass(frozen=True)
class ConflictSettings:
    """`[conflicts]` settings of a knowledge base."""

    enabled: bool = True
    judge_mode: str = "list"
    candidates_per_record: int = 5
    min_similarity: float = 0.5
    max_pairs_per_source: int = 40
    # Pairwise confirmation of conflicting listwise verdicts (list mode only).
    confirm: bool = True
    confirm_min_confidence: float = 0.5
    link_min_confidence: float = 0.7
    validation_retries: int = 1
    # Also compare cases with cases (incompatible confirmed causes or outcomes).
    compare_cases: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(getattr(self, name), bool) for name in ("enabled", "confirm", "compare_cases")):
            raise ValueError("conflicts enabled, confirm and compare_cases must be booleans")
        if self.judge_mode not in JUDGE_MODES:
            raise ValueError(f"judge_mode must be one of {', '.join(JUDGE_MODES)}")
        if not 1 <= self.candidates_per_record <= MAX_CANDIDATES:
            raise ValueError(f"candidates_per_record must be between 1 and {MAX_CANDIDATES}")
        if self.max_pairs_per_source < 1 or self.validation_retries < 0:
            raise ValueError("max_pairs_per_source must be positive and validation_retries >= 0")
        for name in ("min_similarity", "confirm_min_confidence", "link_min_confidence"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class ConflictPolicy:
    """What a knowledge base treats as scope, anchors and judge instructions.

    `prompt` must contain {NEW} and {CANDIDATES}. Hard keys must be equal (or
    unset on one side) for two records to be compared; soft keys only rank
    candidates. Anchors are regular expressions for identifiers whose
    presence in both records makes a candidate worth judging.
    """

    prompt: str
    hard_keys: tuple[str, ...] = ()
    soft_keys: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    isolation_key: str | None = DEFAULT_ISOLATION_KEY
    _anchor_res: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        missing = [name for name in ("{NEW}", "{CANDIDATES}") if name not in self.prompt]
        if missing:
            raise ValueError("Conflict prompt must contain " + " and ".join(missing))
        object.__setattr__(self, "_anchor_res", tuple(re.compile(p, re.IGNORECASE) for p in self.anchors))

    def anchors_of(self, text: str) -> set[str]:
        return {
            _normalized(match.group(0))
            for pattern in self._anchor_res
            for match in pattern.finditer(text)
        }


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _scope(record: KnowledgeRecord, isolation_key: str | None) -> dict[str, Any]:
    return {
        key: value for key, value in record.context.items()
        if key not in _UNSCOPED and key != isolation_key and value not in (None, "")
    }


def _evidence_quotes(record: KnowledgeRecord) -> list[str]:
    # Case Cards keep their quotes in the card, other records in context.quotes.
    card = record.context.get("case_card")
    quotes = card.get("evidence") if isinstance(card, dict) else record.context.get("quotes")
    if not isinstance(quotes, list):
        return []
    texts = [str(quote.get("quote") or "" if isinstance(quote, dict) else quote or "") for quote in quotes]
    return [text[:MAX_QUOTE_CHARACTERS] for text in texts if text][:MAX_EVIDENCE_QUOTES]


def describe(record: KnowledgeRecord, isolation_key: str | None = DEFAULT_ISOLATION_KEY) -> str:
    """What the judge sees of a record (claims render steps and case cards)."""

    lines = [f"[{record.type.value}, {record.status.value}] {record.subject}"]
    if scope := _scope(record, isolation_key):
        lines.append("scope: " + json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str))
    validity = ", ".join(
        f"{name} {value.isoformat()}"
        for name, value in (("valid from", record.valid_from), ("valid to", record.valid_to))
        if value is not None
    )
    if validity:
        lines.append(validity)
    lines.append(record.claim)
    if quotes := _evidence_quotes(record):
        lines.append("evidence quotes: " + json.dumps(quotes, ensure_ascii=False))
    return "\n".join(lines)


def content_hash(record: KnowledgeRecord) -> str:
    """Hash of what a judgement depends on; a change means the pair is judged again."""

    content = {
        "subject": record.subject, "claim": record.claim, "type": record.type.value,
        "context": record.context, "valid_from": record.valid_from, "valid_to": record.valid_to,
    }
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def stale(row: ConflictRow, records: Mapping[UUID, KnowledgeRecord]) -> bool:
    """Whether either record changed since the row was judged (a missing record counts as unchanged)."""

    return any(
        record_id in records and content_hash(records[record_id]) != stored
        for record_id, stored in ((row.a_id, row.a_hash), (row.b_id, row.b_hash))
    )


def quote_in(quote: str, text: str) -> bool:
    quote = _normalized(quote)
    return len(quote) >= MIN_QUOTE_CHARACTERS and quote in _normalized(text)


@dataclass
class _Pair:
    new: KnowledgeRecord
    old: KnowledgeRecord

    @property
    def key(self) -> tuple[UUID, UUID]:
        return ordered(self.new.id, self.old.id)


@dataclass
class _Outcome:
    status: str
    list_verdict: dict[str, Any] | None = None
    pair_verdict: dict[str, Any] | None = None
    final: Verdict | None = None
    link: bool = False


class ConflictDetector:
    """Detects suspected conflicts between one source's records and the KB."""

    def __init__(
        self,
        client_factory: Callable[[], Any],
        *,
        settings: ConflictSettings,
        policy: ConflictPolicy,
        model: Mapping[str, Any] | None = None,
    ) -> None:
        """`client_factory` builds the judge client on first use; `model`
        (resolved, non-secret model settings) enters the judge fingerprint."""

        self._client_factory = client_factory
        self._client: Any = None
        self.settings = settings
        self.policy = policy
        self.fingerprint = hashlib.sha256(json.dumps({
            "prompt": policy.prompt, "model": dict(model or {}),
            "judge_mode": settings.judge_mode,
            "confirm": settings.confirm and settings.judge_mode == "list",
        }, sort_keys=True, default=str).encode()).hexdigest()
        self._calls = 0
        self._usage: dict[str, int] | None = None

    # ----------------------------------------------------------------- blocking

    def _filters(self, record: KnowledgeRecord) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = []
        if record.evidence_id is not None:
            conditions.append({"evidence_id": {"$ne": record.evidence_id}})
        if key := self.policy.isolation_key:
            field_name = f"context.{key}"
            value = record.context.get(key)
            conditions.append(
                {field_name: {"$eq": str(value)}} if value not in (None, "")
                else {field_name: {"$exists": False}}
            )
        for key in self.policy.hard_keys:
            value = record.context.get(key)
            if value in (None, ""):
                continue
            field_name = f"context.{key}"
            conditions.append({"$or": [{field_name: {"$eq": str(value)}}, {field_name: {"$exists": False}}]})
        if record.valid_from is not None:
            conditions.append({"$or": [{"valid_to": {"$exists": False}}, {"valid_to": {"$gte": record.valid_from}}]})
        if record.valid_to is not None:
            conditions.append({"$or": [{"valid_from": {"$exists": False}}, {"valid_from": {"$lte": record.valid_to}}]})
        return {"$and": conditions} if len(conditions) > 1 else conditions[0] if conditions else None

    def comparable(self, new: KnowledgeRecord, old: KnowledgeRecord) -> bool:
        """L0/L1 in Python, so a candidate source cannot widen the SQL filter."""

        if new.id == old.id or (new.evidence_id is not None and new.evidence_id == old.evidence_id):
            return False
        if old.status is KnowledgeStatus.DEPRECATED or old.superseded_by is not None:
            return False
        if new.type is KnowledgeType.CASE and old.type is KnowledgeType.CASE and not self.settings.compare_cases:
            return False
        if key := self.policy.isolation_key:
            if _text(new.context.get(key)) != _text(old.context.get(key)):
                return False
        for key in self.policy.hard_keys:
            a, b = _text(new.context.get(key)), _text(old.context.get(key))
            if a is not None and b is not None and a != b:
                return False
        return _overlaps(new.valid_from, new.valid_to, old.valid_from, old.valid_to)

    async def candidates(self, store: Any, record: KnowledgeRecord) -> list[tuple[KnowledgeRecord, dict[str, Any]]]:
        """Top-K comparable records for one new record, with why each was kept."""

        k = self.settings.candidates_per_record
        types = LESSON_TYPES if record.type is KnowledgeType.CASE and not self.settings.compare_cases else ()
        filters = self._filters(record)
        vector = await store.similar_knowledge(
            record, k=2 * k, types=types, filters=filters, min_similarity=self.settings.min_similarity,
        )
        anchors = self.policy.anchors_of(f"{record.subject}\n{record.claim}")
        lexical = await store.lexical_knowledge(
            " ".join([record.subject, *sorted(anchors)]), k=2 * k, types=types, filters=filters,
        )
        pool: dict[UUID, KnowledgeRecord] = {}
        score: dict[UUID, float] = {}
        signals: dict[UUID, dict[str, Any]] = {}
        for source, hits in (("vector", vector), ("lexical", lexical)):
            for rank, (other, value) in enumerate(hits, 1):
                if not self.comparable(record, other):
                    continue
                pool.setdefault(other.id, other)
                score[other.id] = score.get(other.id, 0.0) + 1.0 / (RRF_RANK_CONSTANT + rank)
                signals.setdefault(other.id, {})[source] = round(float(value), 4)
        ranked = []
        for other_id, other in pool.items():
            shared = sorted(anchors & self.policy.anchors_of(f"{other.subject}\n{other.claim}"))
            soft = [
                key for key in self.policy.soft_keys
                if record.context.get(key) not in (None, "") and record.context.get(key) == other.context.get(key)
            ]
            if shared:
                signals[other_id]["anchors"] = shared
            if soft:
                signals[other_id]["soft_keys"] = soft
            boost = len(soft) / RRF_RANK_CONSTANT
            ranked.append((not shared, -(score[other_id] + boost), str(other_id), other))
        ranked.sort(key=lambda item: item[:3])
        return [(other, signals[other.id]) for *_, other in ranked[:k]]

    @staticmethod
    def cap(
        ranked: Mapping[UUID, Sequence[KnowledgeRecord]], limit: int,
    ) -> tuple[dict[UUID, list[KnowledgeRecord]], int]:
        """Keep at most `limit` pairs, taking each new record's best candidates in turn."""

        kept: dict[UUID, list[KnowledgeRecord]] = {record_id: [] for record_id in ranked}
        total = sum(len(candidates) for candidates in ranked.values())
        taken, depth = 0, 0
        while taken < limit and taken < total:
            for record_id, candidates in ranked.items():
                if depth < len(candidates) and taken < limit:
                    kept[record_id].append(candidates[depth])
                    taken += 1
            depth += 1
        return kept, total - taken

    # -------------------------------------------------------------------- judge

    def _client_or_build(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def _ask(self, new: KnowledgeRecord, candidates: list[tuple[str, KnowledgeRecord]]) -> Verdicts:
        key = self.policy.isolation_key
        listed = "\n\n".join(f"{label}\n{describe(record, key)}" for label, record in candidates)
        prompt = self.policy.prompt.replace("{NEW}", describe(new, key), 1).replace("{CANDIDATES}", listed, 1)
        self._calls += 1
        response = await llm.ainvoke(self._client_or_build(), prompt, error=ConflictRequestError)
        self._count_usage(response)
        payload = llm.response_json(response, task="conflict judgement", env_prefix="CONSOLIDATION")
        return llm.validated(Verdicts, payload, task="conflict judgement")

    def _count_usage(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None) or {}
        if not usage:
            return
        self._usage = self._usage or {"input_tokens": 0, "output_tokens": 0}
        for key in self._usage:
            self._usage[key] += int(usage.get(key) or 0)

    async def _judge(
        self, new: KnowledgeRecord, candidates: list[tuple[str, KnowledgeRecord]],
    ) -> tuple[dict[str, Verdict], str | None]:
        """One verdict per label; labels without exactly one verdict are missing.

        Malformed or incomplete replies are asked again up to
        validation_retries times; request failures propagate (the job retries).
        """

        labels = {label for label, _ in candidates}
        found: dict[str, Verdict] = {}
        error = None
        for _ in range(self.settings.validation_retries + 1):
            try:
                reply = await self._ask(new, candidates)
            except llm.LLMResponseError as exc:
                error = str(exc)[:500]
                continue
            counts: dict[str, int] = {}
            for verdict in reply.verdicts:
                counts[verdict.candidate] = counts.get(verdict.candidate, 0) + 1
            for verdict in reply.verdicts:
                if verdict.candidate in labels and counts[verdict.candidate] == 1:
                    found.setdefault(verdict.candidate, verdict)
            if labels <= found.keys():
                return found, None
            error = "missing or repeated verdicts: " + ", ".join(sorted(labels - found.keys()))
        return found, error

    def _quotes_ok(self, verdict: Verdict, pair: _Pair) -> bool:
        key = self.policy.isolation_key
        return quote_in(verdict.quote_new, describe(pair.new, key)) and quote_in(verdict.quote_old, describe(pair.old, key))

    async def _confirm(self, pair: _Pair) -> tuple[Verdict | None, str | None]:
        found, error = await self._judge(pair.new, [("E1", pair.old)])
        return found.get("E1"), error

    async def _decide_list(self, new: KnowledgeRecord, pairs: list[_Pair]) -> list[_Outcome]:
        settings = self.settings
        seed = int(hashlib.sha256(f"{new.id}:{self.fingerprint}".encode()).hexdigest()[:8], 16)
        order = list(range(len(pairs)))
        random.Random(seed).shuffle(order)
        labels = {index: f"E{position}" for position, index in enumerate(order, 1)}
        found, error = await self._judge(new, [(labels[index], pairs[index].old) for index in order])
        outcomes = []
        for index, pair in enumerate(pairs):
            verdict = found.get(labels[index])
            if verdict is None:
                outcomes.append(_Outcome("pending", list_verdict={"label": labels[index], "seed": seed, "error": error}))
                continue
            list_verdict = {**verdict.model_dump(mode="json"), "label": labels[index], "seed": seed}
            outcome = _Outcome("no_conflict", list_verdict=list_verdict, final=verdict)
            if verdict.relation in CONFLICTING:
                outcome.status = "open"
                if not self._quotes_ok(verdict, pair):
                    outcome.status = "needs_review"
                elif not settings.confirm:
                    outcome.link = verdict.confidence >= settings.link_min_confidence
                elif verdict.confidence >= settings.confirm_min_confidence:
                    confirmed, confirm_error = await self._confirm(pair)
                    if confirmed is None:
                        outcome.pair_verdict = {"error": confirm_error}
                    else:
                        outcome.pair_verdict = confirmed.model_dump(mode="json")
                        outcome.final = confirmed
                        quotes_ok = self._quotes_ok(confirmed, pair)
                        outcome.link = (
                            confirmed.relation in CONFLICTING and quotes_ok
                            and confirmed.confidence >= settings.link_min_confidence
                        )
                        if confirmed.relation in CONFLICTING and not quotes_ok:
                            outcome.status = "needs_review"
            outcomes.append(outcome)
        return outcomes

    async def _decide_pair(self, pair: _Pair) -> _Outcome:
        verdict, error = await self._confirm(pair)
        if verdict is None:
            return _Outcome("pending", pair_verdict={"error": error})
        outcome = _Outcome("no_conflict", pair_verdict=verdict.model_dump(mode="json"), final=verdict)
        if verdict.relation in CONFLICTING:
            if not self._quotes_ok(verdict, pair):
                outcome.status = "needs_review"
            else:
                outcome.status = "open"
                outcome.link = verdict.confidence >= self.settings.link_min_confidence
        return outcome

    # -------------------------------------------------------------------- store

    async def _link(self, store: Any, pair: _Pair, outcome: _Outcome, job_id: str | None) -> None:
        verdict = outcome.final
        await set_links(store, pair.new, pair.old, linked=True, event={
            "at": datetime.now(timezone.utc).isoformat(), "action": "conflict_detected",
            "relation": verdict.relation.value, "aspect": verdict.aspect.value,
            "confidence": verdict.confidence, "reason": verdict.reason,
            "judge_fingerprint": self.fingerprint, "job_id": job_id,
        })

    async def _unlink(self, store: Any, pair: _Pair, outcome: _Outcome, job_id: str | None) -> None:
        verdict = outcome.final
        await set_links(store, pair.new, pair.old, linked=False, event={
            "at": datetime.now(timezone.utc).isoformat(), "action": "conflict_cleared",
            "relation": verdict.relation.value if verdict else None, "status": outcome.status,
            "reason": "judged again after a content change or judge update",
            "judge_fingerprint": self.fingerprint, "job_id": job_id,
        })

    async def _record(self, store: Any, conflicts: Any, pair: _Pair, outcome: _Outcome,
                      job_id: str | None, previous: ConflictRow | None = None) -> ConflictRow:
        a_id, b_id = pair.key
        records = {pair.new.id: pair.new, pair.old.id: pair.old}
        verdict = outcome.final
        conflicting = verdict is not None and verdict.relation in CONFLICTING
        # Links first: a crash before the row is stored only costs a re-judgement.
        if outcome.link:
            await self._link(store, pair, outcome, job_id)
        elif previous is not None and previous.linked and outcome.status != "pending":
            await self._unlink(store, pair, outcome, job_id)
        quotes = {}
        if verdict is not None and (verdict.quote_new or verdict.quote_old):
            quotes = {str(pair.new.id): verdict.quote_new, str(pair.old.id): verdict.quote_old}
        return await conflicts.put(ConflictRow(
            a_id=a_id, b_id=b_id,
            a_hash=content_hash(records[a_id]), b_hash=content_hash(records[b_id]),
            judge_fingerprint=self.fingerprint, status=outcome.status, new_id=pair.new.id,
            relation=verdict.relation.value if verdict else None,
            aspect=verdict.aspect.value if verdict and conflicting else None,
            confidence=verdict.confidence if verdict else None,
            reason=verdict.reason if verdict else None, quotes=quotes,
            list_verdict=outcome.list_verdict, pair_verdict=outcome.pair_verdict,
            linked=outcome.link, job_id=job_id,
        ))

    # ---------------------------------------------------------------------- run

    async def adetect(
        self, store: Any, conflicts: Any, evidence_id: UUID, *, job_id: str | None = None,
    ) -> dict[str, Any]:
        """Judge one source's searchable records against the KB; returns the job result.

        `store` is the KB's knowledge store, `conflicts` its conflict store.
        """

        self._calls, self._usage = 0, None
        result: dict[str, Any] = {
            "evidence_id": str(evidence_id), "judge_mode": self.settings.judge_mode,
            "judge_fingerprint": self.fingerprint, "settings": asdict(self.settings),
        }
        records = [
            record for record in await store.knowledge_for_evidence(evidence_id, embeddings=True)
            if record.status is not KnowledgeStatus.DEPRECATED and record.superseded_by is None
        ]
        result["records"] = len(records)
        if not records:
            return {**result, "skipped": "no searchable records"}

        ranked = {record.id: [other for other, _ in await self.candidates(store, record)] for record in records}
        kept, dropped = self.cap(ranked, self.settings.max_pairs_per_source)
        by_id = {record.id: record for record in records}
        pairs = [_Pair(by_id[record_id], old) for record_id, olds in kept.items() for old in olds]
        rechecked, closed = await self._edited_pairs(store, conflicts, by_id, {pair.key for pair in pairs})
        pairs += rechecked
        keys = [pair.key for pair in pairs + closed]
        stored = await conflicts.get(keys, self.fingerprint)
        latest = await conflicts.current(keys)

        counts = {"no_conflict": 0, "open": 0, "pending": 0, "needs_review": 0, "linked": 0, "reviewed": 0}
        todo: dict[UUID, list[_Pair]] = {}
        cached = 0
        for pair in pairs:
            hashes = {pair.new.id: content_hash(pair.new), pair.old.id: content_hash(pair.old)}
            same = lambda row: (row.a_hash, row.b_hash) == (hashes[row.a_id], hashes[row.b_id])  # noqa: E731
            if (reviewed := latest.get(pair.key)) is not None and reviewed.reviewed and same(reviewed):
                counts["reviewed"] += 1
                continue
            row = stored.get(pair.key)
            if row is not None and row.status in MACHINE_STATUSES and row.status != "pending" and same(row):
                cached += 1
                counts[row.status] += 1
                counts["linked"] += int(row.linked)
                continue
            todo.setdefault(pair.new.id, []).append(pair)

        for record_id, group in todo.items():
            if self.settings.judge_mode == "list":
                outcomes = await self._decide_list(by_id[record_id], group)
            else:
                outcomes = [await self._decide_pair(pair) for pair in group]
            for pair, outcome in zip(group, outcomes, strict=True):
                row = await self._record(store, conflicts, pair, outcome, job_id, latest.get(pair.key))
                counts[row.status if row.status in counts else "reviewed"] += 1
                counts["linked"] += int(row.linked and outcome.link)
        for pair in closed:
            # Edited out of each other's scope: no longer a candidate, the earlier verdict is withdrawn.
            outcome = _Outcome("no_conflict", list_verdict={"closed": "no longer comparable after an edit"})
            await self._record(store, conflicts, pair, outcome, job_id, latest.get(pair.key))
            counts["no_conflict"] += 1

        return {
            **result, "pairs": len(pairs), "judged": sum(map(len, todo.values())), "cached": cached,
            "rechecked": len(rechecked), "closed": len(closed),
            "truncated": dropped > 0, "dropped": dropped, "llm_calls": self._calls,
            "usage": self._usage, **counts,
        }

    async def _edited_pairs(
        self, store: Any, conflicts: Any, records: Mapping[UUID, KnowledgeRecord], kept: set[tuple[UUID, UUID]],
    ) -> tuple[list[_Pair], list[_Pair]]:
        """Pairs of these records outside `kept` whose content changed since the
        current row was judged and whose row still claims a conflict.

        Returns the pairs still comparable (judged again) and those no longer
        comparable (closed without a call).
        """

        # Only rows that still claim something; a pair judged harmless needs a new look only as a candidate.
        rows = [
            row for row in await conflicts.for_records(list(records), ())
            if row.pair not in kept and (row.linked or row.status in ("open", "pending", "needs_review", "confirmed"))
        ]
        if not rows:
            return [], []
        partners = {row.b_id if row.a_id in records else row.a_id for row in rows}
        others = {record.id: record for record in await store.get_knowledge(list(partners))}
        rechecked, closed, seen = [], [], set()
        for row in rows:
            new_id = row.a_id if row.a_id in records else row.b_id
            old = others.get(row.b_id if new_id == row.a_id else row.a_id)
            if old is None or row.pair in seen or not stale(row, {**others, **records}):
                continue
            seen.add(row.pair)
            pair = _Pair(records[new_id], old)
            (rechecked if self.comparable(pair.new, old) else closed).append(pair)
        return rechecked, closed


def link_updates(
    first: KnowledgeRecord, second: KnowledgeRecord, current: Mapping[UUID, KnowledgeRecord], *,
    linked: bool, event: dict[str, Any],
) -> list[KnowledgeLinkUpdate]:
    """Updates adding or withdrawing a pair's conflict links: `contradicted_by`
    (the case) on a lesson paired with a case, otherwise `conflict_with` on
    both records. Records already in the wanted state (per `current`) get none."""

    updates = []
    if (first.type is KnowledgeType.CASE) != (second.type is KnowledgeType.CASE):
        case, lesson = (first, second) if first.type is KnowledgeType.CASE else (second, first)
        if (case.id in getattr(current.get(lesson.id), "contradicted_by", [])) != linked:
            column = "contradicted_by" if linked else "unlink_contradicted_by"
            updates.append(KnowledgeLinkUpdate(lesson.id, **{column: (case.id,)}, event={**event, "case": str(case.id)}))
    else:
        for record, other in ((first, second), (second, first)):
            if (other.id in getattr(current.get(record.id), "conflict_with", [])) != linked:
                column = "conflict_with" if linked else "unlink_conflict_with"
                updates.append(KnowledgeLinkUpdate(record.id, **{column: (other.id,)}, event={**event, "with": str(other.id)}))
    return updates


async def set_links(
    store: Any, first: KnowledgeRecord, second: KnowledgeRecord, *, linked: bool, event: dict[str, Any],
) -> None:
    """Apply `link_updates` against the records as currently stored."""

    current = {record.id: record for record in await store.get_knowledge([first.id, second.id])}
    if updates := link_updates(first, second, current, linked=linked, event=event):
        await store.add_knowledge([], updates=updates)


# Conflict rows an answer warns about: confirmed by a reviewer, or linked by the judge and not dismissed.
NOTE_STATUSES = ("confirmed", "open", "needs_review")


async def conflict_notes(
    store: Any, conflicts: Any, records: Sequence[KnowledgeRecord],
) -> dict[UUID, list[dict[str, Any]]]:
    """Searchable records conflicting with each of `records`, for a warning next to a citation.

    Uses the records' links (`conflict_with`, `contradicted_by`) and the
    conflict table's current rows (confirmed, or linked and still open);
    dismissed or resolved pairs and deprecated partners are left out. Without
    a conflict store (None, or a KB without the table) only links are used.
    """

    ids = {record.id for record in records}
    rows: list[ConflictRow] = []
    if conflicts is not None and ids:
        try:
            rows = await conflicts.for_records(list(ids), ())
        except Exception:  # noqa: BLE001 - a warning must never break an answer
            rows = []
    by_pair = {row.pair: row for row in rows}
    partners: dict[UUID, dict[UUID, ConflictRow | None]] = {record.id: {} for record in records}
    for record in records:
        for other in [*record.conflict_with, *record.contradicted_by]:
            row = by_pair.get(ordered(record.id, other))
            if row is None or row.status not in ("dismissed", "resolved"):
                partners[record.id][other] = row
    for row in rows:
        if row.status == "confirmed" or (row.linked and row.status in NOTE_STATUSES):
            for record_id, other in ((row.a_id, row.b_id), (row.b_id, row.a_id)):
                if record_id in partners:
                    partners[record_id].setdefault(other, row)
    wanted = {other for found in partners.values() for other in found}
    if not wanted:
        return {}
    others = {
        record.id: record for record in await store.get_knowledge(list(wanted))
        if record.status is not KnowledgeStatus.DEPRECATED and record.superseded_by is None
    }
    notes: dict[UUID, list[dict[str, Any]]] = {}
    for record_id, found in partners.items():
        for other_id, row in found.items():
            if (other := others.get(other_id)) is None:
                continue
            notes.setdefault(record_id, []).append({
                "knowledge_id": str(other.id), "subject": other.subject, "claim": other.claim,
                "type": other.type.value, "status": other.status.value,
                "relation": row.relation if row else None, "aspect": row.aspect if row else None,
                "conflict_status": row.status if row else "linked",
                "reason": row.reason if row else None,
            })
    return notes


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _overlaps(a_from: date | None, a_to: date | None, b_from: date | None, b_to: date | None) -> bool:
    if a_from is not None and b_to is not None and b_to < a_from:
        return False
    if b_from is not None and a_to is not None and a_to < b_from:
        return False
    return True
