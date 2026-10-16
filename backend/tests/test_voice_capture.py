"""Offline tests for voice capture: audio files, turns, cleaning, jobs and the API."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
from fastapi.testclient import TestClient

from backend.api.voice_capture import VoiceCaptureService
from backend.app import create_app
from backend.knowledge_bases import get_kb
from backend.src.services.knowledge_extraction import (
    KnowledgeExtractionResult,
    KnowledgeItem,
    KnowledgeType,
)
from backend.src.services.knowledge_ingestion import KnowledgeIngestionService
from backend.src.services.llm import LLMResponseError
from backend.src.services.speech_processing import (
    SpeakerSegment,
    SpeechServiceError,
    TranscriptEvent,
    Utterance,
    format_transcript,
)
from backend.src.services.transcript_cleaning import TranscriptCleaner
from backend.src.services.voice_capture import (
    AudioRecorder,
    CapturedTurn,
    RecordingSession,
    audio_stem,
    build_turns,
    format_turns,
    parse_transcript,
    text_turns,
)
from backend.src.storage import (
    ConversationStatus,
    KnowledgeRecord,
    KnowledgeStatus,
    RawConversation,
)
from backend.tests.test_knowledge_ingestion_service import FakeExtractor
from backend.tests.test_knowledge_ingestion_service import FakeStore as IngestionStore


UTTERANCES = [
    Utterance("Trục Z kêu rè từ sáng.", speaker=1, start=0.5, end=2.0),
    Utterance("Đã kiểm tra độ rơ 0,05 mm chưa?", speaker=2, start=2.4, end=4.0),
    Utterance("Chưa kiểm tra.", speaker=1, start=4.5, end=5.0),
]


# Diarization of UTTERANCES: who spoke when.
SEGMENTS = [SpeakerSegment(0.4, 2.1, 1), SpeakerSegment(2.3, 4.1, 2), SpeakerSegment(4.4, 5.1, 1)]


class FakeSpeechClient:
    """Realtime stream that yields scripted events (in its first session), then
    optionally fails; file recognition and diarization return fixed results."""

    def __init__(self, events=(), *, fail_after: int | None = None, file_utterances=(),
                 segments=SEGMENTS, diarization_error: Exception | None = None):
        self.settings = SimpleNamespace(language="vi-VN", endpointing_ms=800)
        self.events = list(events)
        self.fail_after = fail_after
        self.file_utterances = list(file_utterances)
        self.segments = list(segments)
        self.diarization_error = diarization_error
        self.received = b""
        self.transcribed: list[Path] = []
        self.sessions: list[dict] = []
        self.diarized = 0

    async def aready(self):
        return {"ready": True}

    async def astream(self, frames, *, sample_rate=16000, language=None, **options):
        self.sessions.append(options)
        events, self.events = self.events, []

        async def consume():
            async for chunk in frames:
                self.received += chunk

        reader = asyncio.create_task(consume())
        for index, event in enumerate(events):
            if self.fail_after is not None and index == self.fail_after:
                reader.cancel()
                raise SpeechServiceError("socket closed")
            await asyncio.sleep(0)
            yield event
        await reader

    async def atranscribe_segments(self, path, *, language=None):
        self.transcribed.append(Path(path))
        return [Utterance(u.text, None, u.start, u.end) for u in self.file_utterances]

    async def adiarize(self, wav, *, mode="offline"):
        self.diarized += 1
        if self.diarization_error is not None:
            raise self.diarization_error
        return self.segments

    async def arecover(self, path, stretches, *, language=None):
        self.recovered_stretches = list(stretches)
        return [Utterance("bị sót", None, start + 0.1, end - 0.1) for start, end in stretches]


class SessionScript:
    """Realtime sessions that each give one final after a number of frames."""

    def __init__(self, *finals: tuple[int, Utterance]):
        self.settings = SimpleNamespace(language="vi-VN", endpointing_ms=800)
        self.finals = list(finals)
        self.frames: list[int] = []

    async def astream(self, frames, *, sample_rate=16000, language=None, **_):
        script = self.finals.pop(0) if self.finals else None
        self.frames.append(0)
        async for _chunk in frames:
            self.frames[-1] += 1
            if script and self.frames[-1] == script[0]:
                yield TranscriptEvent("completed", script[1].text, (script[1],))

    async def adiarize(self, wav, *, mode="offline"):
        return []


def final(*utterances: Utterance) -> TranscriptEvent:
    return TranscriptEvent("completed", " ".join(u.text for u in utterances), tuple(utterances))


class AudioAndTurnsTest(unittest.TestCase):
    def test_audio_name_follows_conversation_name(self) -> None:
        started = datetime(2026, 10, 15, 14, 32, 5)
        self.assertEqual(
            audio_stem("Kiểm tra rung trục Z – Đầu phay", started),
            "20261015-143205_kiem-tra-rung-truc-z-dau-phay",
        )
        self.assertEqual(audio_stem("!!!", started), "20261015-143205_hoi-thoai")

    def test_recorder_keeps_whole_samples_and_a_valid_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            recorder = AudioRecorder(path, 16000)
            self.assertEqual(recorder.write(b"\x01\x00\x02"), b"\x01\x00")
            self.assertEqual(recorder.write(b"\x00"), b"\x02\x00")
            # Readable before close: the header is patched after each write.
            with wave.open(str(path), "rb") as reader:
                self.assertEqual(reader.getnframes(), 2)
            recorder.close()
            existing = AudioRecorder.existing(path)
            self.assertEqual((existing.sample_rate, existing.frames), (16000, 2))
            self.assertEqual(len(existing.sha256()), 64)

    def test_rejects_unsupported_rates_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                AudioRecorder(Path(tmp) / "a.wav", 4000)
            bad = Path(tmp) / "bad.wav"
            bad.write_bytes(b"not audio")
            with self.assertRaises(ValueError):
                AudioRecorder.existing(bad)

    def test_turn_ids_match_the_stored_transcript(self) -> None:
        utterances = [*UTTERANCES[:1], Utterance("Rè to hơn khi chạy nhanh.", speaker=1)]
        utterances += UTTERANCES[1:]
        transcript = format_transcript(utterances)
        self.assertEqual(transcript.splitlines()[0],
                         "[T01] Speaker A: Trục Z kêu rè từ sáng. Rè to hơn khi chạy nhanh.")
        built = build_turns(utterances)
        parsed = parse_transcript(transcript)
        self.assertEqual([(t.id, t.speaker, t.text) for t in built],
                         [(t.id, t.speaker, t.text) for t in parsed])
        self.assertEqual(built[1].start, 2.4)

    def test_unknown_speaker_round_trips(self) -> None:
        (turn,) = parse_transcript(format_transcript([Utterance("ồn quá", speaker=None)]))
        self.assertEqual((turn.id, turn.speaker, turn.text), ("T01", None, "ồn quá"))


class TypedTextTest(unittest.TestCase):
    def test_one_author_keeps_every_word_per_paragraph(self) -> None:
        turns, names = text_turns("Ghi chú: thay dầu.\r\n\r\nBước 1: tắt máy\nBước 2: mở nắp\n")
        self.assertEqual(names, {"A": None})
        self.assertEqual(
            format_turns(turns),
            "[T01] Speaker A: Ghi chú: thay dầu.\n[T02] Speaker A: Bước 1: tắt máy\nBước 2: mở nắp",
        )
        self.assertEqual([t.text for t in parse_transcript(format_turns(turns))],
                         [t.text for t in turns])
        lines, _ = text_turns("một\nhai")
        self.assertEqual([t.id for t in lines], ["T01", "T02"])

    def test_dialogue_letters_names_in_order_and_keeps_them_as_names(self) -> None:
        turns, names = text_turns(
            "Anh Minh: Trục Z kêu.\nChị Lan: Từ bao giờ?\n- từ ca đêm\nanh  minh: Từ sáng.\nB: ừ",
            dialogue=True,
        )
        self.assertEqual(names, {"A": "Anh Minh", "B": "Chị Lan", "C": None})
        self.assertEqual([(t.id, t.speaker) for t in turns],
                         [("T01", "A"), ("T02", "B"), ("T03", "A"), ("T04", "C")])
        self.assertEqual(turns[1].text, "Từ bao giờ?\n- từ ca đêm")
        with self.assertRaises(ValueError):
            text_turns("xin chào\nAnh: hi", dialogue=True)

    def test_a_pasted_transcript_keeps_its_speakers(self) -> None:
        turns, names = text_turns("[T03] Speaker B: x\n[T07] Unknown speaker: y", dialogue=True)
        self.assertEqual([(t.id, t.speaker, t.text) for t in turns],
                         [("T01", "B", "x"), ("T02", None, "y")])
        self.assertEqual(names, {"B": None})
        self.assertEqual(text_turns("  \n "), ([], {}))


class RecordingSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.events: list[dict] = []

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def sink(self, event: dict) -> None:
        self.events.append(event)

    def session(self, client: FakeSpeechClient) -> RecordingSession:
        recorder = AudioRecorder(Path(self.tmp.name) / "r.wav", 16000)
        return RecordingSession(client, recorder, self.sink)

    async def test_realtime_finals_become_the_transcript(self) -> None:
        unlabelled = [Utterance(u.text, None, u.start, u.end) for u in UTTERANCES]
        client = FakeSpeechClient([
            TranscriptEvent("delta", "Trục"), final(unlabelled[0]), final(*unlabelled[1:]),
        ])
        session = self.session(client)
        session.start()
        session.feed(b"\x00\x01" * 800)
        outcome = await session.finish()
        self.assertEqual(outcome.path, "realtime")
        # Speakers come from diarizing the whole file, not from the realtime sessions.
        self.assertEqual(outcome.utterances, UTTERANCES)
        self.assertIsNone(outcome.diarization_error)
        self.assertEqual(client.sessions[0]["diarization"], False)
        self.assertEqual(client.sessions[0]["word_timestamps"], True)
        self.assertEqual(client.received, b"\x00\x01" * 800)
        self.assertEqual(self.events[0], {"type": "partial", "text": "Trục"})
        self.assertEqual(self.events[1], {"type": "turns", "turns": [
            {"id": "T01", "speaker": None, "text": "Trục Z kêu rè từ sáng.", "start": 0.5, "end": 2.0},
        ]})
        self.assertGreaterEqual(client.diarized, 1)
        self.assertTrue(session.recorder.closed)

    async def test_a_new_session_starts_in_the_quiet_after_a_final(self) -> None:
        loud = (np.random.default_rng(0).integers(-9000, 9000, 1280).astype("<i2")).tobytes()
        quiet = b"\x00\x00" * 1280  # 80 ms each
        client = SessionScript(
            (3, Utterance("một", None, 0.1, 0.3)), (9, Utterance("hai", None, 0.1, 0.2)),
        )
        session = self.session(client)
        session.start()
        for chunk in [loud] * 6 + [quiet] * 10 + [loud] * 6:
            session.feed(chunk)
            await asyncio.sleep(0.005)
        outcome = await session.finish()
        # Session 1 ends once 0.6 s of quiet followed its final (6 loud + 8 quiet
        # frames). Session 2 is primed with the last 0.48 s of that quiet (6
        # frames), then gets the 2 remaining quiet and 6 loud frames.
        self.assertEqual(client.frames, [14, 14])
        self.assertEqual(outcome.sessions, 2)
        # Its times count from the priming audio: (14 - 6) * 0.08 + 0.1.
        self.assertEqual([(u.text, round(u.start, 2)) for u in outcome.utterances],
                         [("một", 0.1), ("hai", 0.74)])
        self.assertEqual(session.recorder.frames, 22 * 1280)

    async def test_speech_missed_by_recognition_is_recovered_and_labelled(self) -> None:
        client = FakeSpeechClient(
            [final(Utterance(UTTERANCES[0].text, None, 0.5, 2.0))],
            # The diarizer also heard speaker 2 at 6-8 s, where no word was recognized.
            segments=[SpeakerSegment(0.4, 2.1, 1), SpeakerSegment(6.0, 8.0, 2)],
        )
        session = self.session(client)
        session.start()
        session.feed(b"\x00\x01" * 800)
        outcome = await session.finish()
        self.assertEqual(client.recovered_stretches, [(6.0, 8.0)])
        self.assertEqual(outcome.recovered, 1)
        self.assertEqual([(u.text, u.speaker) for u in outcome.utterances],
                         [(UTTERANCES[0].text, 1), ("bị sót", 2)])

    async def test_failed_diarization_keeps_turns_without_letters(self) -> None:
        client = FakeSpeechClient([final(UTTERANCES[0])],
                                  diarization_error=SpeechServiceError("HTTP 501"))
        session = self.session(client)
        session.start()
        session.feed(b"\x00\x01" * 800)
        outcome = await session.finish()
        self.assertEqual([u.speaker for u in outcome.utterances], [None])
        self.assertIn("HTTP 501", outcome.diarization_error)

    async def test_lost_recognition_keeps_recording_and_transcribes_the_file(self) -> None:
        client = FakeSpeechClient([final(UTTERANCES[0]), final(UTTERANCES[1])], fail_after=1,
                                  file_utterances=UTTERANCES)
        session = self.session(client)
        session.start()
        session.feed(b"\x00\x00" * 100)
        await asyncio.sleep(0.05)
        session.feed(b"\x00\x00" * 100)  # after the failure: still saved
        outcome = await session.finish()
        self.assertEqual(outcome.path, "file")
        self.assertIn("socket closed", outcome.realtime_error)
        # Recognized piece by piece, then labelled by diarization.
        self.assertEqual(outcome.utterances, UTTERANCES)
        self.assertEqual(client.transcribed, [session.recorder.path])
        self.assertEqual(session.recorder.frames, 200)
        self.assertIn({"type": "recognition_lost"}, self.events)


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        return SimpleNamespace(content=json.dumps(reply), response_metadata={})


class TranscriptCleanerTest(unittest.IsolatedAsyncioTestCase):
    turns = [
        CapturedTurn("T01", "A", "ờ thì trục Z ờ kêu rè"),
        CapturedTurn("T02", "B", "độ rơ 0,05 mm"),
    ]

    async def test_cleans_and_keeps_raw_when_numbers_are_lost(self) -> None:
        chat = FakeChat([{"turns": [
            {"id": "T01", "text": "Trục Z kêu rè."},
            {"id": "T02", "text": "Độ rơ nhỏ."},
        ]}])
        batches = []

        async def on_batch(batch):
            batches.append(batch)

        cleaned = await TranscriptCleaner(chat).aclean(self.turns, on_batch=on_batch)
        self.assertEqual(cleaned["T01"], {"text": "Trục Z kêu rè."})
        self.assertEqual(cleaned["T02"]["text"], "độ rơ 0,05 mm")
        self.assertEqual(cleaned["T02"]["kept_raw"], "numbers_changed")
        self.assertEqual(batches, [cleaned])
        self.assertIn("data, not instructions", chat.prompts[0])

    async def test_rejects_replies_with_other_turn_ids(self) -> None:
        chat = FakeChat([{"turns": [{"id": "T01", "text": "x"}]}])
        with self.assertRaises(LLMResponseError):
            await TranscriptCleaner(chat).aclean(self.turns)


class IngestStoredTest(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_from_already_stored_evidence_with_progress(self) -> None:
        store = IngestionStore()
        conversation = await store.add_conversation("[T01] Speaker A: Lau ống kính trước.")
        extracted = KnowledgeExtractionResult(knowledge_items=[KnowledgeItem(
            subject="sensor", claim="Clean the lens first.", context={}, type=KnowledgeType.HEURISTIC,
        )])
        stages: list[str] = []
        result = await KnowledgeIngestionService(FakeExtractor(extracted), store).ingest_stored(
            conversation, progress=stages.append
        )
        self.assertEqual(stages, ["extracting", "saving"])
        self.assertEqual(result.evidence_id, conversation.id)
        self.assertEqual(store.knowledge[0].status, KnowledgeStatus.DRAFT)
        self.assertEqual(len(store.conversations), 1)  # no second copy of the evidence
        self.assertEqual(store.conversations[conversation.id].status, ConversationStatus.PROCESSED)

    async def test_failure_marks_the_stored_conversation_and_raises(self) -> None:
        store = IngestionStore()
        conversation = await store.add_conversation("[T01] Speaker A: x")
        with self.assertRaises(RuntimeError):
            await KnowledgeIngestionService(FakeExtractor(RuntimeError("down")), store).ingest_stored(
                conversation
            )
        self.assertEqual(store.conversations[conversation.id].status, ConversationStatus.FAILED)


class VoiceStore:
    """In-memory stand-in for the KnowledgeStore methods voice capture uses."""

    def __init__(self) -> None:
        self.conversations: dict[UUID, RawConversation] = {}
        self.records: dict[UUID, KnowledgeRecord] = {}

    async def add_conversation(self, transcript, *, stated_by=None, metadata=None):
        conversation = RawConversation(
            id=uuid4(), transcript=transcript, stated_by=stated_by,
            metadata=json.loads(json.dumps(metadata or {})),
            created_at=datetime.now(timezone.utc),
        )
        self.conversations[conversation.id] = conversation
        return conversation

    async def get_conversation(self, conversation_id):
        return self.conversations.get(conversation_id)

    async def update_conversation_metadata(self, conversation_id, patch):
        conversation = self.conversations[conversation_id]
        metadata = {**conversation.metadata, **json.loads(json.dumps(patch, default=str))}
        self.conversations[conversation_id] = conversation.model_copy(update={"metadata": metadata})
        return True

    async def knowledge_for_evidence(self, conversation_id):
        return [r for r in self.records.values() if r.evidence_id == conversation_id]

    async def get_knowledge(self, ids):
        return [self.records[i] for i in ids if i in self.records]

    async def list_conversations(self, *, metadata_filters, metadata_keys, limit=50):
        return [
            {"id": c.id, "status": c.status.value, "error": c.error, "created_at": c.created_at,
             "metadata": {k: v for k, v in c.metadata.items() if k in metadata_keys},
             "knowledge_counts": {}}
            for c in self.conversations.values()
            if any(all(c.metadata.get(k) == v for k, v in f.items()) for f in metadata_filters)
        ]

    async def review_knowledge(self, record_id, status, *, event):
        record = self.records.get(record_id)
        if record is None:
            return None
        history = [*record.revision.get("history", []), dict(event)]
        record = record.model_copy(update={"status": status, "revision": {"history": history}})
        self.records[record_id] = record
        return record

    async def aclose(self):
        pass


class FakeIngestion:
    def __init__(self, store: VoiceStore, gate: asyncio.Event | None = None) -> None:
        self.store = store
        self.gate = gate

    async def ingest_stored(self, conversation, *, progress=None, **_):
        progress and progress("extracting")
        if self.gate is not None:
            await self.gate.wait()
        record = KnowledgeRecord(
            subject="trục Z", claim="Kiểm tra độ rơ trước khi thay ổ bi.",
            type=KnowledgeType.HEURISTIC, evidence_id=conversation.id,
            context={"certainty": "stated", "quotes": [{"turn_id": "T02", "quote": "độ rơ"}]},
        )
        self.store.records[record.id] = record
        return SimpleNamespace(records=(record,), merged=(), consolidation={"add": 1})


class VoiceCaptureApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = VoiceStore()
        self.speech = FakeSpeechClient([final(UTTERANCES[0]), final(*UTTERANCES[1:])])

        async def open_store(spec):
            return self.store

        self.service = VoiceCaptureService(
            {"general": get_kb("general")},
            audio_dir=Path(self.tmp.name),
            speech_client=lambda: self.speech,
            open_store=open_store,
            ingestion_factory=lambda spec, store: (FakeIngestion(store), {"version": "test"}),
            cleaner_factory=lambda: TranscriptCleaner(FakeChat([{"turns": [
                {"id": "T01", "text": "Trục Z kêu rè từ sáng."},
                {"id": "T02", "text": "Đã kiểm tra độ rơ 0,05 mm chưa?"},
                {"id": "T03", "text": "Chưa kiểm tra."},
            ]}])),
        )
        self.client = TestClient(create_app(voice_capture=self.service))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def record(self, name="Rung trục Z", clean=True) -> dict:
        with self.client.websocket_connect("/v1/voice/general/record") as socket:
            socket.send_text(json.dumps({"type": "start", "name": name,
                                         "sample_rate": 16000, "clean": clean}))
            ready = socket.receive_json()
            self.assertEqual(ready["type"], "ready")
            socket.send_bytes(b"\x00\x00" * 1600)
            socket.send_text(json.dumps({"type": "stop"}))
            events = []
            while (event := socket.receive_json())["type"] not in {"saved", "error"}:
                events.append(event)
            return event

    def wait_for(self, conversation_id: str, predicate) -> dict:
        for _ in range(100):
            detail = self.client.get(f"/v1/voice/general/conversations/{conversation_id}").json()
            if predicate(detail):
                return detail
            asyncio.run(asyncio.sleep(0.02))
        self.fail(f"condition not reached: {detail}")

    def test_record_stores_audio_transcript_and_extracts_drafts(self) -> None:
        saved = self.record()
        self.assertEqual(saved["type"], "saved")
        conversation = self.store.conversations[UUID(saved["conversation_id"])]
        self.assertTrue(conversation.transcript.startswith("[T01] Speaker A: Trục Z"))
        metadata = conversation.metadata
        self.assertEqual(metadata["source_kind"], "voice_recording")
        self.assertEqual(metadata["speakers"], {"A": {"name": None}, "B": {"name": None}})
        audio = Path(self.tmp.name) / metadata["audio"]["file"]
        self.assertTrue(audio.name.endswith("_rung-truc-z.wav"))
        with wave.open(str(audio), "rb") as reader:
            self.assertEqual(reader.getnframes(), 1600)
        self.assertTrue((Path(self.tmp.name) / metadata["audio"]["asr_file"]).is_file())

        detail = self.wait_for(saved["conversation_id"], lambda d: (
            d["ingestion"]["status"] == "done" and d["cleaning"]["status"] == "done"
        ))
        self.assertEqual([t["id"] for t in detail["turns"]], ["T01", "T02", "T03"])
        self.assertEqual(detail["turns"][1]["start"], 2.4)
        self.assertEqual(detail["records"][0]["status"], "draft")
        self.assertEqual(detail["cleaning"]["turns"]["T01"]["text"], "Trục Z kêu rè từ sáng.")
        self.assertEqual(detail["ingestion"]["stored"], 1)
        listed = self.client.get("/v1/voice/general/conversations").json()["conversations"]
        self.assertEqual(listed[0]["name"], "Rung trục Z")

    def test_typed_dialogue_is_stored_and_extracted_without_audio(self) -> None:
        response = self.client.post("/v1/voice/general/texts", json={
            "name": " Ca đêm ", "text": "Anh Minh: Trục Z kêu.\nChị Lan: Từ bao giờ?", "dialogue": True,
        })
        self.assertEqual(response.status_code, 201)
        conversation_id = response.json()["conversation_id"]
        conversation = self.store.conversations[UUID(conversation_id)]
        self.assertEqual(conversation.transcript,
                         "[T01] Speaker A: Trục Z kêu.\n[T02] Speaker B: Từ bao giờ?")
        self.assertEqual(conversation.metadata["source_kind"], "typed_text")
        self.assertNotIn("audio", conversation.metadata)

        detail = self.wait_for(conversation_id, lambda d: d["ingestion"]["status"] == "done")
        self.assertEqual(detail["source"], "text")
        self.assertEqual(detail["name"], "Ca đêm")
        self.assertEqual(detail["speakers"], [{"label": "A", "name": "Anh Minh"},
                                              {"label": "B", "name": "Chị Lan"}])
        self.assertFalse(detail["audio"]["available"])
        self.assertEqual(detail["cleaning"]["status"], "off")
        self.assertEqual(detail["records"][0]["status"], "draft")
        clean = self.client.post(f"/v1/voice/general/conversations/{conversation_id}/clean")
        self.assertEqual(clean.status_code, 409)
        self.assertEqual(
            self.client.get(f"/v1/voice/general/conversations/{conversation_id}/audio").status_code,
            404,
        )

        self.record()
        listed = self.client.get("/v1/voice/general/conversations").json()["conversations"]
        self.assertEqual(sorted(item["source"] for item in listed), ["text", "voice"])

    def test_typed_text_is_validated(self) -> None:
        url = "/v1/voice/general/texts"
        self.assertEqual(self.client.post(url, json={"name": "a", "text": " \n "}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"name": " ", "text": "x"}).status_code, 422)
        self.assertEqual(self.client.post(url, json={
            "name": "a", "text": "không có tên", "dialogue": True}).status_code, 422)
        self.assertEqual(self.client.post("/v1/voice/maintenance/texts",
                                          json={"name": "a", "text": "x"}).status_code, 404)
        self.assertEqual(self.store.conversations, {})

    def test_cleaning_can_be_turned_off_and_started_later(self) -> None:
        saved = self.record(clean=False)
        detail = self.wait_for(saved["conversation_id"], lambda d: d["ingestion"]["status"] == "done")
        self.assertEqual(detail["cleaning"]["status"], "off")
        response = self.client.post(f"/v1/voice/general/conversations/{saved['conversation_id']}/clean")
        self.assertEqual(response.status_code, 202)
        self.wait_for(saved["conversation_id"], lambda d: d["cleaning"]["status"] == "done")

    def test_speakers_are_named_and_records_reviewed(self) -> None:
        saved = self.record()
        detail = self.wait_for(saved["conversation_id"], lambda d: d["records"])
        url = f"/v1/voice/general/conversations/{saved['conversation_id']}/speakers"
        self.assertEqual(self.client.patch(url, json={"speakers": {"Z": "x"}}).status_code, 422)
        renamed = self.client.patch(url, json={"speakers": {"A": "  Anh Minh "}}).json()
        self.assertEqual(renamed["speakers"][0], {"label": "A", "name": "Anh Minh"})

        record_id = detail["records"][0]["id"]
        reviewed = self.client.post(f"/v1/voice/general/knowledge/{record_id}/review",
                                    json={"decision": "reject", "note": "sai ý"}).json()
        self.assertEqual(reviewed["status"], "deprecated")
        self.assertEqual(reviewed["history"][-1]["note"], "sai ý")
        missing = self.client.post(f"/v1/voice/general/knowledge/{uuid4()}/review",
                                   json={"decision": "approve"})
        self.assertEqual(missing.status_code, 404)

    def test_failed_extraction_keeps_the_recording_and_can_be_retried(self) -> None:
        def broken(spec, store):
            raise ValueError("Missing required LLM configuration: OPENROUTER_API_KEY")

        self.service._ingestion_factory = broken
        saved = self.record(clean=False)
        detail = self.wait_for(saved["conversation_id"], lambda d: d["ingestion"]["status"] == "failed")
        self.assertIn("OPENROUTER_API_KEY", detail["ingestion"]["error"])
        self.assertEqual(len(detail["turns"]), 3)

        self.service._ingestion_factory = lambda spec, store: (FakeIngestion(store), {})
        url = f"/v1/voice/general/conversations/{saved['conversation_id']}/extract"
        self.assertEqual(self.client.post(url).status_code, 202)
        self.wait_for(saved["conversation_id"], lambda d: d["ingestion"]["status"] == "done")
        self.assertEqual(self.client.post(url).status_code, 409)

    def test_silence_is_not_stored_but_its_audio_is_kept(self) -> None:
        self.speech.events = []
        self.speech.segments = []  # the diarizer heard nobody either
        event = self.record()
        self.assertEqual((event["type"], event["code"]), ("error", "empty"))
        self.assertFalse(self.store.conversations)
        self.assertTrue((Path(self.tmp.name) / event["audio_file"]).is_file())

    def test_unknown_kb_and_unready_speech_service_are_refused(self) -> None:
        with self.client.websocket_connect("/v1/voice/maintenance/record") as socket:
            self.assertEqual(socket.receive_json()["code"], "kb")

        async def down():
            raise SpeechServiceError("connection refused")

        self.speech.aready = down
        with self.client.websocket_connect("/v1/voice/general/record") as socket:
            socket.send_text(json.dumps({"type": "start", "name": "x", "sample_rate": 16000}))
            self.assertEqual(socket.receive_json()["code"], "speech_unavailable")
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_disconnect_without_stop_keeps_audio_unlinked(self) -> None:
        with self.client.websocket_connect("/v1/voice/general/record") as socket:
            socket.send_text(json.dumps({"type": "start", "name": "x", "sample_rate": 16000}))
            socket.receive_json()
            socket.send_bytes(b"\x00\x00" * 160)
        for _ in range(100):
            files = [p.name for p in Path(self.tmp.name).iterdir()]
            if any(name.endswith(".interrupted.wav") for name in files):
                break
            asyncio.run(asyncio.sleep(0.02))
        self.assertTrue(any(name.endswith(".interrupted.wav") for name in files), files)
        self.assertFalse(self.store.conversations)

    def test_upload_transcribes_a_whole_wav(self) -> None:
        self.speech.file_utterances = UTTERANCES
        wav = Path(self.tmp.name) / "upload-src.wav"
        recorder = AudioRecorder(wav, 16000)
        recorder.write(b"\x00\x00" * 320)
        recorder.close()
        with wav.open("rb") as handle:
            response = self.client.post(
                "/v1/voice/general/recordings",
                data={"name": "Mất mạng giữa chừng", "clean": "false"},
                files={"audio": ("recording.wav", handle, "audio/wav")},
            )
        self.assertEqual(response.status_code, 201, response.text)
        conversation = self.store.conversations[UUID(response.json()["conversation_id"])]
        self.assertEqual(conversation.metadata["capture_mode"], "upload")
        self.assertEqual(conversation.metadata["asr"]["recognition"], "file")

        bad = self.client.post("/v1/voice/general/recordings", data={"name": "x"},
                               files={"audio": ("a.wav", b"nope", "audio/wav")})
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
