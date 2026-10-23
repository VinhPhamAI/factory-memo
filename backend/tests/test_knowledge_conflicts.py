"""Conflict stage: blocking, listwise judging with confirmation, caching and wiring, with fakes only."""

import asyncio
import json
import re
import tempfile
import unittest
from dataclasses import asdict
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend.app import create_app
from backend.api.voice_capture import VoiceCaptureService
from backend.knowledge_bases import get_kb
from backend.knowledge_bases.industrial_qa.conflicts import CONFLICT_ANCHORS, HARD_SCOPE_KEYS, SOFT_SCOPE_KEYS
from backend.src.services.job_worker import StageWorker
from backend.src.services.knowledge_conflicts import (
    ConflictDetector, ConflictPolicy, ConflictRequestError, ConflictSettings, conflict_notes, content_hash,
)
from backend.src.services.knowledge_consolidation import PROMPT_NO_CONFLICT, Decisions, KnowledgeConsolidator
from backend.src.services.knowledge_extraction import KnowledgeExtractionResult, KnowledgeItem, KnowledgeType
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.services.knowledge_management import KnowledgeManagementService, job_state
from backend.src.storage.document_uploads import DocumentUploadStore
from backend.src.storage.job_queue import STAGES, MemoryJobQueue
from backend.src.storage.knowledge_conflicts import ConflictRow, MemoryConflictStore, ordered
from backend.src.storage.models import KnowledgeRecord, KnowledgeStatus
from backend.tests.test_knowledge_ingestion_service import FakeExtractor
from backend.tests.test_knowledge_management import ChunkStore, SourceStore, memory_runs


PROMPT = (Path(__file__).parents[1] / "knowledge_bases/industrial_qa/conflict.md").read_text(encoding="utf-8")
NEW_SOURCE, OLD_SOURCE = uuid4(), uuid4()


def record(subject, claim, *, type=KnowledgeType.FACT, evidence=OLD_SOURCE, **options):
    return KnowledgeRecord(subject=subject, claim=claim, type=type, evidence_id=evidence, **options)


# ------------------------------------------------------------------ fakes


def _matches(record, filters):
    """The store's filter grammar (`$and`, `$or`, `$eq`, `$ne`, `$exists`, `$gte`, `$lte`)."""

    if filters is None:
        return True
    if "$and" in filters:
        return all(_matches(record, item) for item in filters["$and"])
    if "$or" in filters:
        return any(_matches(record, item) for item in filters["$or"])
    (field, condition), = filters.items()
    value = record.context.get(field[8:]) if field.startswith("context.") else getattr(record, field)
    text = None if value in (None, "") else str(value)
    (operator, expected), = condition.items()
    return {
        "$eq": lambda: text == str(expected),
        "$ne": lambda: text != str(expected),
        "$exists": lambda: (text is not None) == expected,
        "$gte": lambda: value is not None and value >= expected,
        "$lte": lambda: value is not None and value <= expected,
    }[operator]()


class ConflictKnowledgeStore:
    """Searchable records ranked by insertion order; filters applied like SQL (or ignored)."""

    def __init__(self, *records, apply_filters=True):
        self.records = {record.id: record for record in records}
        self.apply_filters = apply_filters
        self.updates = []

    def add(self, *records):
        self.records.update({record.id: record for record in records})

    def _search(self, record, k, types, filters):
        found = [
            other for other in self.records.values()
            if other.id != getattr(record, "id", None)
            and other.status is not KnowledgeStatus.DEPRECATED and other.superseded_by is None
            and (not types or other.type in types)
            and (not self.apply_filters or _matches(other, filters))
        ]
        return found[:k]

    async def knowledge_for_evidence(self, evidence_id, *, embeddings=False):
        return [record for record in self.records.values() if record.evidence_id == evidence_id]

    async def similar_knowledge(self, record, *, k, types=(), filters=None, min_similarity=0.0):
        return [(other, 0.9) for other in self._search(record, k, types, filters)]

    async def lexical_knowledge(self, query, *, k, types=(), filters=None):
        words = set(query.casefold().split())
        hits = [other for other in self._search(None, 100, types, filters)
                if words & set(other.subject.casefold().split())]
        return [(other, 0.5) for other in hits[:k]]

    async def get_knowledge(self, ids):
        return [self.records[i] for i in ids if i in self.records]

    async def aclose(self):
        pass

    async def add_knowledge(self, records, *, updates=()):
        assert not records
        for update in updates:
            self.updates.append(update)
            stored = self.records[update.record_id]
            changes = {
                column: [value for value in dict.fromkeys([*getattr(stored, column), *getattr(update, column)])
                         if value not in getattr(update, f"unlink_{column}", ())]
                for column in ("conflict_with", "contradicted_by")
            }
            if update.superseded_by is not None:
                changes.update(superseded_by=update.superseded_by, status=KnowledgeStatus.DEPRECATED)
            if update.event is not None:
                history = [*stored.revision.get("history", []), update.event]
                changes["revision"] = {**stored.revision, "history": history}
            self.records[stored.id] = stored.model_copy(update=changes)
        return []


_BLOCK = re.compile(r"^(E\d+)\n\[[^\]]*\] (.+)$", re.MULTILINE)


class Judge:
    """Answers each labelled candidate with `rule(new_subject, old_subject, asked)`.

    `asked` counts earlier questions about the same pair (0: listwise, 1:
    confirmation). The rule returns a verdict dict without `candidate`, or
    None to leave the candidate out of the reply.
    """

    def __init__(self, rule, *, fail=0):
        self.rule = rule
        self.fail = fail
        self.prompts = []
        self.asked = {}

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("endpoint down")
        new = re.search(r"## New record\n\n\[[^\]]*\] (.+)", prompt).group(1)
        candidates = prompt.split("## Candidates\n\n", 1)[1]
        verdicts = []
        for label, old in _BLOCK.findall(candidates):
            asked = self.asked.get((new, old), 0)
            self.asked[(new, old)] = asked + 1
            if (verdict := self.rule(new, old, asked)) is not None:
                verdicts.append({"candidate": label, **verdict})
        return AIMessage(content=json.dumps({"verdicts": verdicts}))


def unrelated(new, old, asked):
    return {"relation": "unrelated", "confidence": 0.9}


def contradiction(quote_new, quote_old, confidence=0.9, relation="contradiction"):
    return {"relation": relation, "aspect": "value", "quote_new": quote_new, "quote_old": quote_old,
            "confidence": confidence, "reason": "different values"}


def detector(judge, **settings):
    policy = ConflictPolicy(PROMPT, hard_keys=HARD_SCOPE_KEYS, soft_keys=SOFT_SCOPE_KEYS, anchors=CONFLICT_ANCHORS)
    return ConflictDetector(lambda: judge, settings=ConflictSettings(**settings), policy=policy,
                            model={"model": "fake"})


def run(coroutine):
    return asyncio.run(coroutine)


# ------------------------------------------------------------------ detector


class ConflictDetectorTest(unittest.TestCase):
    def setUp(self):
        self.new = record("PA01 control mode", "Set PA01 to 1000 for position mode on MR-JE.",
                          evidence=NEW_SOURCE, context={"machine_model": "MR-JE-10A"})
        self.old = record("PA01 control mode", "Set PA01 to 0000 for position mode on MR-JE.",
                          context={"machine_model": "MR-JE-10A"})
        self.conflicts = MemoryConflictStore()

    def contradicting(self, new, old, asked):
        if old == self.old.subject:
            return contradiction("PA01 to 1000", "PA01 to 0000")
        return unrelated(new, old, asked)

    def test_blocking_keeps_scope_isolation_validity_and_never_compares_cases(self):
        kept = record("PA01 unset model", "PA01 defaults to 0000.")
        candidates = [
            self.old, kept,
            record("PA01 other model", "PA01 is 1000.", context={"machine_model": "MR-J4-10A"}),
            record("PA01 same source", "PA01 is 1000.", evidence=NEW_SOURCE),
            record("PA01 deprecated", "PA01 is 0000.", status=KnowledgeStatus.DEPRECATED),
            record("PA01 other run", "PA01 is 0000.", context={"benchmark_run_id": "r1"}),
            record("PA01 expired", "PA01 was 0000.", valid_to=date(2020, 1, 1)),
        ]
        new = self.new.model_copy(update={"valid_from": date(2024, 1, 1)})
        case = record("PA01 incident", "Drive alarmed after PA01 change.", type=KnowledgeType.CASE, evidence=NEW_SOURCE)
        old_case = record("PA01 old incident", "Drive alarmed.", type=KnowledgeType.CASE)
        for apply_filters in (True, False):  # the Python re-check alone gives the same set
            with self.subTest(apply_filters=apply_filters):
                store = ConflictKnowledgeStore(new, case, *candidates, old_case, apply_filters=apply_filters)
                found = run(detector(Judge(unrelated)).candidates(store, new))
                # A lesson may be compared with a stored case; a case never with a case.
                self.assertEqual({other.id for other, _ in found}, {self.old.id, kept.id, old_case.id})
                self.assertIn("pa01", found[0][1]["anchors"])
                case_found = run(detector(Judge(unrelated)).candidates(store, case))
                self.assertNotIn(old_case.id, {other.id for other, _ in case_found})
                self.assertTrue(all(other.type is not KnowledgeType.CASE for other, _ in case_found))

    def test_cap_takes_candidates_round_robin_and_reports_truncation(self):
        a, b = uuid4(), uuid4()
        kept, dropped = ConflictDetector.cap({a: ["a1", "a2", "a3"], b: ["b1"]}, 3)
        self.assertEqual((kept, dropped), ({a: ["a1", "a2"], b: ["b1"]}, 1))
        olds = [record(f"PA01 note {n}", f"PA01 note {n}.") for n in range(3)]
        store = ConflictKnowledgeStore(self.new, *olds)
        result = run(detector(Judge(unrelated), max_pairs_per_source=2).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["pairs"], result["truncated"], result["dropped"]), (2, True, 1))
        self.assertEqual(len(self.conflicts.rows), 2)

    def test_confirmed_conflict_links_both_lessons_and_cached_pairs_are_not_judged_again(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        judge = Judge(self.contradicting)
        result = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE, job_id="j1"))
        self.assertEqual((result["open"], result["linked"], result["llm_calls"]), (1, 1, 2))
        self.assertEqual(store.records[self.new.id].conflict_with, [self.old.id])
        self.assertEqual(store.records[self.old.id].conflict_with, [self.new.id])
        self.assertEqual(store.records[self.new.id].status, KnowledgeStatus.DRAFT)
        row, = self.conflicts.rows.values()
        self.assertEqual((row.pair, row.status, row.new_id, row.linked), (ordered(self.new.id, self.old.id), "open",
                                                                           self.new.id, True))
        self.assertTrue(row.to_json()["confirmed_by_pair"])
        self.assertEqual(row.quotes[str(self.new.id)], "PA01 to 1000")

        again = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((again["cached"], again["judged"], again["llm_calls"], again["linked"]), (1, 0, 0, 1))
        self.assertEqual(len(store.updates), 2)
        # Changed content is judged again.
        store.add(self.old.model_copy(update={"claim": "Set PA01 to 0000 (default) for position mode on MR-JE."}))
        changed = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((changed["judged"], changed["cached"]), (1, 0))

    def test_a_retried_job_neither_duplicates_links_nor_loses_the_judgement(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        with self.assertRaises(ConflictRequestError):
            run(detector(Judge(self.contradicting, fail=1)).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual(self.conflicts.rows, {})

        class CrashOnce(MemoryConflictStore):
            crashed = False

            async def put(self, row):
                if not self.crashed:
                    self.crashed = True
                    raise RuntimeError("worker died after linking")
                return await super().put(row)

        conflicts = CrashOnce()
        with self.assertRaises(RuntimeError):
            run(detector(Judge(self.contradicting)).adetect(store, conflicts, NEW_SOURCE))
        self.assertEqual(len(store.updates), 2)  # linked before the row was stored
        result = run(detector(Judge(self.contradicting)).adetect(store, conflicts, NEW_SOURCE))
        self.assertEqual((result["linked"], len(store.updates)), (1, 2))
        self.assertEqual(store.records[self.old.id].conflict_with, [self.new.id])
        self.assertEqual(next(iter(conflicts.rows.values())).status, "open")

    def test_a_quote_from_the_wrong_record_needs_review_without_confirmation(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        judge = Judge(lambda new, old, asked: contradiction("PA01 to 0000", "PA01 to 1000"))
        result = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["needs_review"], result["linked"], result["llm_calls"]), (1, 0, 1))
        self.assertEqual(store.updates, [])

    def test_a_missing_verdict_stays_pending_and_is_judged_on_the_next_run(self):
        other = record("PA01 wiring", "PA01 wiring note.")
        store = ConflictKnowledgeStore(self.new, self.old, other)
        judge = Judge(lambda new, old, asked: None if old == other.subject else unrelated(new, old, asked))
        result = run(detector(judge, validation_retries=1).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["pending"], result["no_conflict"], result["llm_calls"]), (1, 1, 2))
        pending = self.conflicts.rows[(*ordered(self.new.id, other.id), judge_fingerprint := result["judge_fingerprint"])]
        self.assertIn("missing or repeated verdicts", pending.list_verdict["error"])
        again = run(detector(Judge(unrelated)).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((again["judged"], again["cached"], again["no_conflict"]), (1, 1, 2))
        self.assertEqual(self.conflicts.rows[(*ordered(self.new.id, other.id), judge_fingerprint)].status,
                         "no_conflict")

    def test_list_and_pair_disagreement_stays_open_without_links(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        judge = Judge(lambda new, old, asked: contradiction("PA01 to 1000", "PA01 to 0000") if asked == 0
                      else {"relation": "refinement", "confidence": 0.8})
        result = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["open"], result["linked"], result["llm_calls"]), (1, 0, 2))
        row, = self.conflicts.rows.values()
        self.assertEqual((row.list_verdict["relation"], row.pair_verdict["relation"], row.linked),
                         ("contradiction", "refinement", False))
        self.assertFalse(row.to_json()["confirmed_by_pair"])
        self.assertEqual(store.updates, [])

    def test_a_case_exception_marks_the_lesson_contradicted_by_the_case(self):
        case = record("PA01 incident", "After setting PA01 to 1000 the drive still alarmed.",
                      type=KnowledgeType.CASE, evidence=NEW_SOURCE)
        store = ConflictKnowledgeStore(case, self.old)
        judge = Judge(lambda new, old, asked: contradiction("PA01 to 1000", "PA01 to 0000", relation="case_exception"))
        result = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual(result["linked"], 1)
        self.assertEqual(store.records[self.old.id].contradicted_by, [case.id])
        self.assertEqual((store.records[case.id].conflict_with, store.records[self.old.id].conflict_with), ([], []))

    def test_pair_mode_asks_once_per_pair_under_its_own_fingerprint(self):
        other = record("PA01 wiring", "PA01 wiring note.")
        store = ConflictKnowledgeStore(self.new, self.old, other)
        listwise, pairwise = detector(Judge(unrelated)), detector(Judge(self.contradicting), judge_mode="pair")
        self.assertNotEqual(listwise.fingerprint, pairwise.fingerprint)
        result = run(pairwise.adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["llm_calls"], result["open"], result["linked"], result["no_conflict"]), (2, 1, 1, 1))
        self.assertTrue(all("E2" not in prompt.split("## Candidates", 1)[1] for prompt in pairwise._client.prompts))

    def test_a_reviewer_decision_is_kept_and_not_judged_again(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        judge = Judge(self.contradicting)
        found = detector(judge)
        a, b = ordered(self.new.id, self.old.id)
        records = {self.new.id: self.new, self.old.id: self.old}
        run(self.conflicts.put(ConflictRow(a_id=a, b_id=b, a_hash=content_hash(records[a]),
                                           b_hash=content_hash(records[b]), judge_fingerprint=found.fingerprint,
                                           status="dismissed")))
        result = run(found.adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["reviewed"], result["llm_calls"]), (1, 0))
        self.assertEqual(next(iter(self.conflicts.rows.values())).status, "dismissed")

    def test_a_review_holds_under_any_fingerprint_until_an_edit_reopens_it(self):
        store = ConflictKnowledgeStore(self.new, self.old)
        found = detector(Judge(self.contradicting))
        first = run(found.adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual(first["linked"], 1)
        row, = self.conflicts.rows.values()
        event = {"decision": "dismiss"}
        run(self.conflicts.set_review(row.id, "dismissed", linked=False, event=event))
        # Another prompt or model: the reviewer's decision still holds.
        other = detector(Judge(self.contradicting), judge_mode="pair")
        result = run(other.adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["reviewed"], result["llm_calls"]), (1, 0))
        # An edit reopens the pair; the review history stays on the row.
        store.add(self.old.model_copy(update={"claim": "Set PA01 to 0000 only for position mode on MR-JE."}))
        again = run(found.adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((again["reviewed"], again["judged"], again["open"]), (0, 1, 1))
        row, = self.conflicts.rows.values()
        self.assertEqual((row.status, row.review), ("open", [event]))
        self.assertEqual(run(self.conflicts.current([row.pair]))[row.pair].status, "open")

    def stale_link(self, far):
        """A linked open row between the new record and `far`, judged on older content."""

        a, b = ordered(self.new.id, far.id)
        row = run(self.conflicts.put(ConflictRow(a_id=a, b_id=b, a_hash="old", b_hash="old",
                                                 judge_fingerprint="earlier", status="open", linked=True)))
        new = self.new.model_copy(update={"conflict_with": [far.id]})
        return row, new, far.model_copy(update={"conflict_with": [self.new.id]})

    def test_an_edited_linked_pair_outside_the_top_k_is_judged_again_and_unlinked(self):
        far = record("Spindle note", "Spindle grease every 500 hours.", context={"machine_model": "MR-JE-10A"})
        _, new, far = self.stale_link(far)
        store = ConflictKnowledgeStore(new, self.old, far)
        judge = Judge(self.contradicting)
        result = run(detector(judge, candidates_per_record=1).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["rechecked"], result["closed"], result["pairs"]), (1, 0, 2))
        self.assertEqual(store.records[far.id].conflict_with, [])
        self.assertEqual(store.records[self.new.id].conflict_with, [self.old.id])
        self.assertEqual(store.records[far.id].revision["history"][-1]["action"], "conflict_cleared")
        self.assertEqual(run(self.conflicts.current([ordered(self.new.id, far.id)]))[ordered(self.new.id, far.id)]
                         .status, "no_conflict")

    def test_a_pair_edited_out_of_scope_is_closed_without_a_call(self):
        far = record("PA01 other model", "Set PA01 to 0000.", context={"machine_model": "MR-J4-10A"})
        row, new, far = self.stale_link(far)
        store = ConflictKnowledgeStore(new, far)
        judge = Judge(self.contradicting)
        result = run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))
        self.assertEqual((result["closed"], result["llm_calls"], result["no_conflict"]), (1, 0, 1))
        self.assertEqual((store.records[far.id].conflict_with, store.records[self.new.id].conflict_with), ([], []))
        current = run(self.conflicts.current([row.pair]))[row.pair]
        self.assertEqual((current.status, current.linked, current.relation), ("no_conflict", False, None))

    def test_cases_are_compared_with_cases_only_when_enabled(self):
        case = record("PA01 incident", "Alarm AL.16 after the PA01 change; the confirmed cause was a loose encoder cable.",
                      type=KnowledgeType.CASE, evidence=NEW_SOURCE)
        old_case = record("PA01 old incident", "Alarm AL.16 after the PA01 change; the confirmed cause was a worn bearing.",
                          type=KnowledgeType.CASE)
        judge = Judge(lambda new, old, asked: contradiction("loose encoder cable", "worn bearing"))
        store = ConflictKnowledgeStore(case, old_case)
        self.assertEqual(run(detector(judge).adetect(store, self.conflicts, NEW_SOURCE))["pairs"], 0)
        result = run(detector(judge, compare_cases=True).adetect(store, MemoryConflictStore(), NEW_SOURCE))
        self.assertEqual((result["pairs"], result["linked"]), (1, 1))
        self.assertEqual(store.records[old_case.id].conflict_with, [case.id])
        self.assertEqual(store.records[case.id].contradicted_by, [])

    def test_token_usage_is_summed_when_the_endpoint_reports_it(self):
        class Metered(Judge):
            async def ainvoke(self, prompt):
                reply = await super().ainvoke(prompt)
                reply.usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
                return reply

        result = run(detector(Metered(self.contradicting)).adetect(
            ConflictKnowledgeStore(self.new, self.old), self.conflicts, NEW_SOURCE))
        self.assertEqual(result["usage"], {"input_tokens": 200, "output_tokens": 20})
        plain = run(detector(Judge(unrelated)).adetect(
            ConflictKnowledgeStore(self.new, self.old), MemoryConflictStore(), NEW_SOURCE))
        self.assertIsNone(plain["usage"])

    def test_a_source_without_searchable_records_is_skipped(self):
        result = run(detector(Judge(unrelated)).adetect(ConflictKnowledgeStore(self.old), self.conflicts, NEW_SOURCE))
        self.assertEqual(result["skipped"], "no searchable records")

    def test_the_judge_sees_scope_and_evidence_quotes_but_not_the_isolation_key(self):
        new = self.new.model_copy(update={"context": {
            **self.new.context, "benchmark_run_id": "r1", "quotes": [{"turn_id": "T03", "quote": "PA01 auf 1000"}]}})
        old = self.old.model_copy(update={"context": {**self.old.context, "benchmark_run_id": "r1"}})
        judge = Judge(unrelated)
        run(detector(judge).adetect(ConflictKnowledgeStore(new, old), self.conflicts, NEW_SOURCE))
        self.assertIn('"PA01 auf 1000"', judge.prompts[0])
        self.assertIn('"machine_model": "MR-JE-10A"', judge.prompts[0])
        self.assertNotIn("r1", judge.prompts[0])


# ------------------------------------------------------------------ consolidation


class ConsolidationWithoutConflictsTest(unittest.TestCase):
    def test_conflicts_are_left_to_the_conflict_stage(self):
        lesson = record("PA01", "PA01 is 1000.", evidence=NEW_SOURCE)
        case = record("PA01 incident", "Drive alarmed.", type=KnowledgeType.CASE, evidence=NEW_SOURCE)
        stored = record("PA01", "PA01 is 0000.")

        class Store:
            async def similar_knowledge(self, record, **options):
                return [(stored, 0.9)]

        class Client:
            prompts = []

            async def ainvoke(self, prompt):
                self.prompts.append(prompt)
                return AIMessage(content=Decisions.model_validate({
                    "lessons": [{"lesson": 0, "action": "conflict", "target": "E1", "reason": "differs"}],
                    "cases": [{"case": 0, "supports": [], "contradicts": ["E1"]}],
                }).model_dump_json())

        client = Client()
        plan = run(KnowledgeConsolidator(client, Store(), detect_conflicts=False).aplan([lesson, case]))
        self.assertIn(PROMPT_NO_CONFLICT.split("## Existing")[0], client.prompts[0])
        self.assertNotIn('"conflict"', client.prompts[0])
        self.assertEqual((plan.updates, lesson.conflict_with), ([], []))
        self.assertEqual(lesson.revision["consolidation"]["action"], "add")
        self.assertEqual(lesson.revision["consolidation"]["suggested"], "conflict")
        self.assertEqual(plan.summary, {"add": 1})

        legacy = run(KnowledgeConsolidator(client, Store()).aplan([lesson.model_copy(), case.model_copy()]))
        self.assertEqual(sorted(update.record_id == stored.id for update in legacy.updates), [True, True])
        self.assertEqual(legacy.summary, {"conflict": 1, "contradicts": 1})


# ------------------------------------------------------------------ stage wiring


class ConflictJobStateTest(unittest.TestCase):
    def test_a_conflict_job_is_reported_apart_from_the_processing_status(self):
        queue = MemoryJobQueue()
        job = run(queue.enqueue("general", "conflict", "conversation", str(uuid4()), mode="knowledge"))
        state = job_state({"status": "done", "mode": "knowledge"}, job)
        self.assertEqual((state["status"], state["conflict_job"]["stage"]), ("done", "conflict"))
        self.assertNotIn("job", state)


class FakeDetector:
    def __init__(self):
        self.calls = []

    async def adetect(self, store, conflicts, evidence_id, *, job_id=None):
        self.calls.append((evidence_id, job_id))
        return {"evidence_id": str(evidence_id), "judge_mode": "list", "judge_fingerprint": "fp",
                "pairs": 1, "judged": 1, "cached": 0, "truncated": False, "no_conflict": 0, "open": 1,
                "pending": 0, "needs_review": 0, "linked": 1, "reviewed": 0, "llm_calls": 2}


class ConflictStageTest(unittest.TestCase):
    """Extract chains the conflict stage only for knowledge mode in a KB that enables it."""

    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"EMBEDDING_DIM": "16"}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.uploads = DocumentUploadStore(Path(tmp.name) / "uploads")
        self.store, self.chunks, self.detector = SourceStore(), ChunkStore(), FakeDetector()
        extractor = FakeExtractor(KnowledgeExtractionResult(knowledge_items=[KnowledgeItem(
            subject="Trục Z", claim="Chưa xác nhận nguyên nhân.", type=KnowledgeType.FACT,
            context={"quotes": [{"quote": "Chưa xác nhận nguyên nhân."}]},
        )]))
        specs = {"general": get_kb("general")}
        runs = memory_runs(self.store)

        async def open_store(spec):
            return self.store

        async def open_chunks(spec):
            return self.chunks

        async def open_runs(spec, store):
            return runs

        async def open_conflicts(spec, store):
            return MemoryConflictStore()

        capture = VoiceCaptureService(specs, audio_dir=Path(tmp.name) / "audio", open_store=open_store)
        self.queue = MemoryJobQueue(retry_delay_s=0)
        self.manager = KnowledgeManagementService(
            specs, self.uploads, capture, queue=self.queue, open_store=open_store, open_document_store=open_chunks,
            ingestion_factory=lambda spec, store: (KnowledgeIngestionService(extractor, store), {"version": "t"}),
            open_runs=open_runs, conflict_factory=lambda spec: self.detector, open_conflicts=open_conflicts,
        )
        self.worker = StageWorker(self.queue, self.manager, STAGES, name="test-worker")
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={"general": object()}, voice_capture=capture,
            document_uploads=self.uploads, knowledge_management=self.manager,
        )))

    def upload(self, mode):
        response = self.client.post("/v1/documents/general/uploads", data={"extract_mode": mode},
                                    files={"file": (f"{mode}.txt", f"Ghi chú {mode}: chưa xác nhận.".encode(), "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_knowledge_extraction_queues_the_conflict_stage_and_reports_its_summary(self):
        body = self.upload("knowledge")
        self.assertEqual(body["job"]["next_stages"], ["extract", "conflict"])
        self.assertEqual(self.client.portal.call(self.worker.drain), 3)
        detail = self.client.get(f"/v1/knowledge-management/general/sources/upload/{body['id']}").json()
        evidence_id, = self.store.conversations  # created by the extract stage
        self.assertEqual([call[0] for call in self.detector.calls], [evidence_id])
        processing = detail["processing"]
        self.assertEqual((processing["status"], processing["conflict_job"]["stage"]), ("done", "conflict"))
        self.assertEqual((processing["conflicts"]["status"], processing["conflicts"]["open"]), ("done", 1))
        self.assertNotIn("job", processing)

    def test_document_mode_and_disabled_kbs_have_no_conflict_stage(self):
        self.assertEqual(self.upload("documents")["job"]["next_stages"], ["extract"])
        self.manager._conflict_factory = lambda spec: None
        self.assertEqual(self.manager.conflict_stages("general", "knowledge"), ())

    def test_a_legacy_in_process_extraction_queues_the_conflict_check(self):
        evidence_id = uuid4()
        run(self.manager.queue_conflicts("general", evidence_id))
        job, = self.queue.jobs.values()
        self.assertEqual((job.stage, job.source_kind, job.source_id), ("conflict", "conversation", str(evidence_id)))
        run(self.manager.queue_conflicts("general", evidence_id))  # an active job: logged, not raised
        self.assertEqual(len(self.queue.jobs), 1)


class ConflictReviewApiTest(unittest.TestCase):
    """List, review (confirm / dismiss / supersede) and recheck over the KM API, with fakes."""

    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"EMBEDDING_DIM": "16"}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = ConflictDetectorTest()
        base.setUp()
        self.new, self.old = base.new, base.old
        self.store, self.conflicts = ConflictKnowledgeStore(self.new, self.old), MemoryConflictStore()
        self.detector = detector(Judge(base.contradicting))
        run(self.detector.adetect(self.store, self.conflicts, NEW_SOURCE))
        specs = {"general": get_kb("general")}

        async def open_store(spec):
            return self.store

        async def open_conflicts(spec, store):
            return self.conflicts

        capture = VoiceCaptureService(specs, audio_dir=Path(tmp.name) / "audio", open_store=open_store)
        self.queue = MemoryJobQueue(retry_delay_s=0)
        self.manager = KnowledgeManagementService(
            specs, DocumentUploadStore(Path(tmp.name) / "uploads"), capture, queue=self.queue,
            open_store=open_store, conflict_factory=lambda spec: self.detector, open_conflicts=open_conflicts,
        )
        self.client = self.enterContext(TestClient(create_app(
            knowledge_agents={"general": object()}, voice_capture=capture, knowledge_management=self.manager,
        )))
        self.base = "/v1/knowledge-management/general/conflicts"

    def listed(self, **params):
        response = self.client.get(self.base, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def review(self, conflict_id, decision, **body):
        return self.client.post(f"{self.base}/{conflict_id}/review", json={"decision": decision, **body})

    def test_the_list_shows_both_records_quotes_and_counts(self):
        page = self.listed()
        self.assertEqual((page["enabled"], page["counts"], page["next_offset"]), (True, {"open": 1}, None))
        item, = page["conflicts"]
        sides = {item["a"]["id"]: item["a"], item["b"]["id"]: item["b"]}
        self.assertEqual(sides[str(self.new.id)]["quote"], "PA01 to 1000")
        self.assertEqual(sides[str(self.old.id)]["evidence_id"], str(OLD_SOURCE))
        self.assertEqual((item["status"], item["linked"], item["stale"]), ("open", True, False))
        self.assertEqual(len(self.listed(evidence_id=str(NEW_SOURCE))["conflicts"]), 1)
        self.assertEqual(self.listed(evidence_id=str(uuid4()))["conflicts"], [])
        self.assertEqual(self.listed(status="dismissed")["conflicts"], [])
        self.assertEqual(self.client.get(self.base, params={"status": "nope"}).status_code, 422)

    def test_dismiss_withdraws_the_links_and_confirm_restores_them(self):
        conflict_id = self.listed()["conflicts"][0]["id"]
        dismissed = self.review(conflict_id, "dismiss", note="different firmware")
        self.assertEqual(dismissed.status_code, 200, dismissed.text)
        self.assertEqual((dismissed.json()["status"], dismissed.json()["linked"]), ("dismissed", False))
        for record_id in (self.new.id, self.old.id):
            stored = self.store.records[record_id]
            self.assertEqual((stored.conflict_with, stored.status), ([], KnowledgeStatus.DRAFT))
            self.assertEqual(stored.revision["history"][-1]["decision"], "dismiss")
        confirmed = self.review(conflict_id, "confirm").json()
        self.assertEqual((confirmed["status"], len(confirmed["review"])), ("confirmed", 2))
        self.assertEqual(self.store.records[self.old.id].conflict_with, [self.new.id])
        self.assertEqual(self.store.records[self.new.id].status, KnowledgeStatus.DRAFT)
        self.assertEqual(self.listed()["counts"], {"confirmed": 1})

    def test_supersede_retires_the_other_record_and_is_final(self):
        conflict_id = self.listed()["conflicts"][0]["id"]
        self.assertEqual(self.review(conflict_id, "supersede").status_code, 422)
        resolved = self.review(conflict_id, "supersede", keep_id=str(self.new.id))
        self.assertEqual(resolved.json()["status"], "resolved")
        retired = self.store.records[self.old.id]
        self.assertEqual((retired.status, retired.superseded_by), (KnowledgeStatus.DEPRECATED, self.new.id))
        self.assertEqual(self.store.records[self.new.id].revision["history"][-1]["kept"], str(self.new.id))
        self.assertEqual(self.review(conflict_id, "dismiss").status_code, 409)
        self.assertEqual(self.review(uuid4(), "dismiss").status_code, 404)

    def test_an_edited_record_marks_the_row_stale_and_recheck_queues_its_source(self):
        self.store.add(self.old.model_copy(update={"claim": "Set PA01 to 0000 or 1000 for position mode on MR-JE."}))
        self.assertTrue(self.listed()["conflicts"][0]["stale"])
        queued = self.client.post(f"{self.base}/recheck", json={})
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual((queued.json()["stale"], queued.json()["sources"]), (1, 1))
        job, = self.queue.jobs.values()
        self.assertEqual((job.stage, job.source_id), ("conflict", str(OLD_SOURCE)))
        explicit = self.client.post(f"{self.base}/recheck", json={"record_ids": [str(self.old.id)]}).json()
        self.assertEqual((explicit["queued"], explicit["busy"]), ([], [str(OLD_SOURCE)]))
        # The job judges the pair again under the new content.
        run(self.detector.adetect(self.store, self.conflicts, OLD_SOURCE))
        self.assertFalse(self.listed()["conflicts"][0]["stale"])

    def test_a_kb_without_a_conflict_stage_lists_nothing(self):
        self.manager._conflict_factory = lambda spec: None
        self.assertEqual(self.listed(), {"conflicts": [], "counts": {}, "offset": 0, "limit": 50,
                                         "next_offset": None, "enabled": False})
        self.assertEqual(self.client.post(f"{self.base}/recheck", json={}).status_code, 404)

class AnswerConflictNotesTest(unittest.TestCase):
    """Citations of records with an open or confirmed conflict carry the conflicting records."""

    def setUp(self):
        base = ConflictDetectorTest()
        base.setUp()
        self.new, self.old = base.new, base.old
        self.store, self.conflicts = ConflictKnowledgeStore(self.new, self.old), MemoryConflictStore()
        run(detector(Judge(base.contradicting)).adetect(self.store, self.conflicts, NEW_SOURCE))
        self.new = self.store.records[self.new.id]

    def notes(self, conflicts="default"):
        return run(conflict_notes(self.store, self.conflicts if conflicts == "default" else conflicts, [self.new]))

    def test_a_linked_open_conflict_is_noted_with_the_other_record(self):
        note, = self.notes()[self.new.id]
        self.assertEqual((note["knowledge_id"], note["relation"], note["conflict_status"]),
                         (str(self.old.id), "contradiction", "open"))
        self.assertEqual(note["claim"], self.old.claim)
        # Without the conflict table, the record links alone still warn.
        note, = self.notes(None)[self.new.id]
        self.assertEqual((note["knowledge_id"], note["conflict_status"], note["relation"]),
                         (str(self.old.id), "linked", None))

    def test_dismissed_pairs_retired_partners_and_unlinked_suspicions_are_not_noted(self):
        row, = self.conflicts.rows.values()
        run(self.conflicts.set_review(row.id, "dismissed", linked=False, event={}))
        self.assertEqual(self.notes(), {})  # even though the fake still holds the links
        run(self.conflicts.set_review(row.id, "open", linked=False, event={}))
        self.new = self.new.model_copy(update={"conflict_with": []})
        self.assertEqual(self.notes(), {})
        run(self.conflicts.set_review(row.id, "confirmed", linked=True, event={}))
        self.assertEqual(len(self.notes()[self.new.id]), 1)
        self.store.add(self.old.model_copy(update={"status": KnowledgeStatus.DEPRECATED}))
        self.assertEqual(self.notes(), {})

    def test_the_service_adds_notes_to_knowledge_citations_only(self):
        from types import SimpleNamespace
        from backend.knowledge_bases.industrial_qa.service import with_conflicts
        from backend.src.agents.agent.service import DocumentCitation, KnowledgeCitation

        retrieval = SimpleNamespace(sources=[SimpleNamespace(record=self.new)])
        citations = (KnowledgeCitation(str(self.new.id), self.new.subject, self.new.claim, "fact", None, None),
                     DocumentCitation("doc-1", "Manual"))
        noted = run(with_conflicts(citations, retrieval, self.store, self.conflicts))
        self.assertEqual(noted[0].conflicts[0]["knowledge_id"], str(self.old.id))
        self.assertEqual(noted[1], citations[1])
        json.dumps([asdict(c) for c in noted])

        class Broken(ConflictKnowledgeStore):
            async def get_knowledge(self, ids):
                raise ConnectionError("down")

        with self.assertLogs("backend.knowledge_bases.industrial_qa.service", "WARNING"):
            self.assertEqual(run(with_conflicts(citations, retrieval, Broken(), None)), citations)

if __name__ == "__main__":
    unittest.main()
