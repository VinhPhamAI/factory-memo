"""Tests for the NeMo-Speech.cpp client.

Offline tests use a fake WebSocket. Live tests run against a started
speech-api service when SPEECH_API_URL_TEST is set (e.g. http://127.0.0.1:8080)
and use the sample WAVs shipped in the NeMo-Speech.cpp checkout.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from backend.src.services.speech_processing import (
    SilenceTracker,
    SpeakerSegment,
    SpeechClient,
    SpeechServiceError,
    SpeechSettings,
    Utterance,
    Word,
    assign_speakers,
    format_transcript,
    iter_pcm_chunks,
    parse_transcription,
    read_wav_pcm16,
    shift,
    speaker_label,
    speech_pieces,
    split_by_speaker,
    uncovered_speech,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "deployments" / "NeMo-Speech.cpp" / "test_files"
PREFIX = "conversation.item.input_audio_transcription."


def word(text: str, start: float, speaker: int | None) -> Word:
    return Word(text=text, start=start, end=start + 0.3, speaker=speaker)


class SpeakerTurnTest(unittest.TestCase):
    def test_splits_on_speaker_change(self) -> None:
        turns = split_by_speaker([
            word("Máy", 0.0, 1), word("rung.", 0.3, 1),
            word("Kiểm", 1.0, 2), word("tra", 1.3, 2), word("coupling.", 1.6, 2),
        ], language="vi-VN")

        self.assertEqual([(t.speaker, t.text) for t in turns],
                         [(1, "Máy rung."), (2, "Kiểm tra coupling.")])
        self.assertEqual(turns[1].start, 1.0)
        self.assertAlmostEqual(turns[1].end, 1.9)
        self.assertEqual(turns[0].language, "vi-VN")

    def test_untagged_words_keep_no_speaker(self) -> None:
        (turn,) = split_by_speaker([word("hello", 0.0, None)])
        self.assertIsNone(turn.speaker)
        self.assertEqual(format_transcript([turn]), "[T01] Unknown speaker: hello")

    def test_speaker_letters_name_voices_not_people(self) -> None:
        self.assertEqual(
            [speaker_label(n) for n in (1, 2, 4, 26, 27, None, 0)],
            ["A", "B", "D", "Z", "AA", None, None],
        )

    def test_without_words_uses_text(self) -> None:
        self.assertEqual(split_by_speaker([], "  xin chào "), [Utterance(text="xin chào")])
        self.assertEqual(split_by_speaker([], "  "), [])

    def test_format_merges_same_speaker_across_finals(self) -> None:
        text = format_transcript([
            Utterance("A.", speaker=1), Utterance("B.", speaker=1),
            Utterance("C.", speaker=2), Utterance("", speaker=2),
        ])
        self.assertEqual(text, "[T01] Speaker A: A. B.\n[T02] Speaker B: C.")

    def test_monologue_turns_end_at_long_pauses_and_after_thirty_seconds(self) -> None:
        def said(start: float, end: float) -> Utterance:
            return Utterance(f"{start:g}.", speaker=1, start=start, end=end)

        text = format_transcript([
            said(0, 5), said(5.8, 12),  # short pause: one turn
            said(15, 20),  # 3 s pause: new turn
            said(20.5, 40),  # still within 30 s of 15
            said(40.5, 52),  # would span 37 s: new turn
        ])
        self.assertEqual(
            text,
            "[T01] Speaker A: 0. 5.8.\n[T02] Speaker A: 15. 20.5.\n[T03] Speaker A: 40.5.",
        )

    def test_parses_verbose_json(self) -> None:
        turns = parse_transcription({
            "text": "Hi there. Yes.", "language": "en-US",
            "words": [
                {"word": "Hi", "start": 0.0, "end": 0.2, "confidence": 0.9, "speaker": 1},
                {"word": "there.", "start": 0.2, "end": 0.5, "confidence": 0.9, "speaker": 1},
                {"word": "Yes.", "start": 1.0, "end": 1.2, "confidence": 0.8, "speaker": 2},
            ],
        })
        self.assertEqual(format_transcript(turns), "[T01] Speaker A: Hi there.\n[T02] Speaker B: Yes.")

    def test_rejects_malformed_response(self) -> None:
        with self.assertRaises(SpeechServiceError):
            parse_transcription({"words": []})
        with self.assertRaises(SpeechServiceError):
            parse_transcription({"text": "x", "words": [{"word": "x"}]})


class WavTest(unittest.TestCase):
    def test_reads_stereo_as_mono_at_original_rate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.wav"
            frames = np.array([[100, 300], [-100, -300]], dtype="<i2")
            with wave.open(str(path), "wb") as writer:
                writer.setnchannels(2)
                writer.setsampwidth(2)
                writer.setframerate(44100)
                writer.writeframes(frames.tobytes())

            pcm, rate = read_wav_pcm16(path)

        self.assertEqual(rate, 44100)
        self.assertEqual(np.frombuffer(pcm, "<i2").tolist(), [200, -200])

    def test_chunks_cover_audio(self) -> None:
        pcm = bytes(16000 * 2)  # one second at 16 kHz
        chunks = list(iter_pcm_chunks(pcm, 16000, chunk_ms=80))
        self.assertEqual(len(chunks), 13)
        self.assertEqual(sum(map(len, chunks)), len(pcm))


class FakeSocket:
    """Replays server events; `completed` events follow audio, like the server."""

    def __init__(self, finals: list[dict], error: str | None = None) -> None:
        self.sent: list[str | bytes] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.finals = finals
        self.error = error
        self.inbox.put_nowait(json.dumps({"type": "session.created"}))

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        if isinstance(message, bytes):
            if self.error:
                await self.inbox.put(json.dumps(
                    {"type": "error", "error": {"message": self.error}}))
            elif self.finals:
                await self.inbox.put(json.dumps({"type": PREFIX + "delta", "delta": "par"}))
                await self.inbox.put(json.dumps(self.finals.pop(0)))
            return
        event = json.loads(message)
        if event["type"] == "session.update":
            await self.inbox.put(json.dumps({"type": "session.updated", "session": event["session"]}))
        elif event["type"] == "input_audio_buffer.commit":
            await self.inbox.put(json.dumps({"type": "input_audio_buffer.committed"}))

    async def recv(self) -> str:
        return await self.inbox.get()


class StreamTest(unittest.IsolatedAsyncioTestCase):
    async def collect(self, socket: FakeSocket, chunks: list[bytes], **kwargs):
        client = SpeechClient(
            SpeechSettings(base_url="http://speech:8080", timeout_seconds=5),
            connect_ws=lambda url, **_: socket,
        )
        return [event async for event in client.astream(chunks, **kwargs)]

    async def test_session_audio_commit_and_speaker_turns(self) -> None:
        final = {
            "type": PREFIX + "completed", "transcript": "Rung. Kiểm tra.",
            "words": [
                {"word": "Rung.", "start": 0.0, "end": 0.4, "speaker": 1},
                {"word": "Kiểm", "start": 1.0, "end": 1.2, "speaker": 2},
                {"word": "tra.", "start": 1.2, "end": 1.5, "speaker": 2},
            ],
        }
        socket = FakeSocket([final])

        events = await self.collect(
            socket, [b"\x00\x00" * 160], sample_rate=16000, language="vi-VN",
            endpointing_ms=800,
        )

        update = json.loads(socket.sent[0])
        self.assertEqual(update["type"], "session.update")
        self.assertEqual(update["session"]["sample_rate"], 16000)
        self.assertEqual(update["session"]["language"], "vi-VN")
        self.assertTrue(update["session"]["speaker_diarization"])
        self.assertTrue(update["session"]["word_timestamps"])
        self.assertEqual(update["session"]["endpointing_ms"], 800)
        self.assertIsInstance(socket.sent[1], bytes)
        self.assertEqual(json.loads(socket.sent[-1]), {"type": "input_audio_buffer.commit"})
        self.assertEqual([e.kind for e in events], ["delta", "completed"])
        self.assertEqual(format_transcript(events[1].utterances),
                         "[T01] Speaker A: Rung.\n[T02] Speaker B: Kiểm tra.")

    async def test_server_error_raises(self) -> None:
        with self.assertRaisesRegex(SpeechServiceError, "bad audio"):
            await self.collect(FakeSocket([], error="bad audio"), [b"\x00\x00"])

    def test_websocket_url_follows_scheme(self) -> None:
        self.assertEqual(
            SpeechClient(SpeechSettings(base_url="https://host/"))._ws_url,
            "wss://host/v1/audio/transcriptions/realtime",
        )


LIVE_URL = os.getenv("SPEECH_API_URL_TEST")


@unittest.skipUnless(LIVE_URL, "set SPEECH_API_URL_TEST to run against speech-api")
@unittest.skipUnless(SAMPLES.is_dir(), "run deployments/speech/setup.sh for sample audio")
class LiveSpeechServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = SpeechClient(SpeechSettings(base_url=LIVE_URL or "", timeout_seconds=120))

    async def test_ready(self) -> None:
        ready = await self.client.aready()
        self.assertTrue(ready.get("ready"), ready)

    async def test_http_transcription(self) -> None:
        turns = await self.client.atranscribe_file(
            SAMPLES / "asr/wav/test/jfk.wav", language="en-US", diarization=False)
        self.assertIn("ask not", format_transcript(turns).lower())

    async def test_http_diarization_finds_several_speakers(self) -> None:
        turns = await self.client.atranscribe_file(
            SAMPLES / "diar/ami_en2002d_2132.wav", language="en-US")
        self.assertGreaterEqual(len({t.speaker for t in turns if t.speaker}), 2)

    async def test_realtime_stream_emits_finals_with_speakers(self) -> None:
        pcm, rate = read_wav_pcm16(SAMPLES / "diar/ami_en2002d_2132.wav")
        events = [
            event async for event in self.client.astream(
                iter_pcm_chunks(pcm, rate), sample_rate=rate, language="en-US")
        ]
        finals = [e for e in events if e.kind == "completed"]
        self.assertTrue(finals)
        speakers = {u.speaker for e in finals for u in e.utterances if u.speaker}
        self.assertGreaterEqual(len(speakers), 2)


if __name__ == "__main__":
    unittest.main()


def _audio(*parts: tuple[str, float], rate: int = 16000) -> bytes:
    """PCM16 of ("speech" | "pause", seconds) parts; speech is loud noise."""

    rng = np.random.default_rng(1)
    chunks = [
        (rng.integers(-8000, 8000, int(seconds * rate)) if kind == "speech"
         else rng.integers(-30, 30, int(seconds * rate))).astype("<i2").tobytes()
        for kind, seconds in parts
    ]
    return b"".join(chunks)


class LongAudioTest(unittest.TestCase):
    def test_speakers_come_from_diarization_by_word_overlap(self) -> None:
        words = (Word("chào", 0.0, 0.4), Word("anh", 0.5, 0.9), Word("dạ", 1.6, 1.9),
                 Word("xa", 9.0, 9.2))
        labelled = assign_speakers(
            [Utterance("chào anh dạ xa", None, 0.0, 9.2, words)],
            # Diarizer numbers 3 and 5; the first voice heard becomes 1 (A).
            [SpeakerSegment(1.5, 2.5, 5), SpeakerSegment(0.0, 1.0, 3)],
        )
        self.assertEqual([(u.text, u.speaker) for u in labelled],
                         [("chào anh", 1), ("dạ", 2), ("xa", None)])
        # Without words the utterance span decides; a nearby segment counts.
        (near,) = assign_speakers([Utterance("ừ", None, 2.9, 3.2)], [SpeakerSegment(1.5, 2.5, 5)])
        self.assertEqual(near.speaker, 1)

    def test_uncovered_speech_is_what_the_diarizer_heard_without_words(self) -> None:
        said = [Utterance("a b", 1, 1.0, 3.0, (Word("a", 1.0, 1.4), Word("b", 2.6, 3.0)))]
        segments = [SpeakerSegment(0.5, 3.2, 1), SpeakerSegment(5.0, 5.4, 2),
                    SpeakerSegment(6.0, 7.0, 2), SpeakerSegment(7.3, 8.0, 2)]
        # 1.7-2.3 between the words; 5.0-5.4 is too short; 6-7 and 7.3-8 join.
        self.assertEqual(uncovered_speech(said, segments), [(1.7, 2.3), (6.0, 8.0)])
        self.assertEqual(uncovered_speech(said, []), [])

    def test_shift_moves_utterance_and_word_times(self) -> None:
        moved = shift(Utterance("a", 1, 0.5, 1.0, (Word("a", 0.5, 1.0, 1),)), 10)
        self.assertEqual((moved.start, moved.end, moved.words[0].start), (10.5, 11.0, 10.5))

    def test_pieces_split_at_long_pauses_and_skip_silence(self) -> None:
        rate = 16000
        pcm = _audio(("speech", 3), ("pause", 0.8), ("speech", 3), ("pause", 3),
                     ("speech", 2), ("pause", 4))
        pieces = [(round(a / rate, 1), round(b / rate, 1)) for a, b in speech_pieces(pcm, rate)]
        # The short pause joins its neighbours; the 3 s pause separates (cut in
        # its middle, 8.2 s); the second half of the 4 s trailing pause is not sent.
        self.assertEqual(pieces, [(0.0, 8.2), (8.2, 13.8)])
        long = speech_pieces(_audio(("speech", 70)), rate)
        self.assertTrue(all((b - a) / rate <= 30.0 for a, b in long))
        self.assertEqual(long[-1][1], 70 * rate)

    def test_silence_tracker_counts_trailing_quiet_only(self) -> None:
        tracker = SilenceTracker(16000)
        speech = _audio(("speech", 0.08))
        pause = _audio(("pause", 0.08))
        for chunk in [speech] * 10:
            tracker.add(chunk)
        self.assertEqual(tracker.quiet_seconds, 0.0)
        for chunk in [pause] * 5:
            tracker.add(chunk)
        self.assertAlmostEqual(tracker.quiet_seconds, 0.4)
        tracker.add(speech)
        self.assertEqual(tracker.quiet_seconds, 0.0)
