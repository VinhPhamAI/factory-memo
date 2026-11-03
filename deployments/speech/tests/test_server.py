"""Offline tests of the VieGem-ASR speech server (no GPU or model weights).

A stub engine stands in for the model: it "recognizes" a word wherever the
test audio has a loud 0.3 s tone, so endpointing, partials, splitting and the
HTTP/WebSocket contracts can be checked exactly. A stub VAD likewise rates
loud 32 ms chunks as speech.

    uv run python -m unittest discover -s deployments/speech/tests -t deployments/speech -v
"""

from __future__ import annotations

import asyncio
import io
import json
import unittest
import wave

import numpy as np
from fastapi.testclient import TestClient

from viegem_server.app import Settings, create_app
from viegem_server.audio import MODEL_RATE, resample, split_long
from viegem_server.engine import Transcription, Word, before, ctc_words
from viegem_server.realtime import EVENT_PREFIX, RealtimeSession, SessionConfig
from viegem_server.vad import (
    CONTEXT, ChunkVad, Region, RegionTracker, StreamResampler, VadStream,
)

WORD_S = 0.3
FRAME = 0.02


def tone_pcm(layout: list[float | None], rate: int = 16_000) -> bytes:
    """PCM16 audio: each entry is a 0.3 s word (any number) or None for 0.3 s of silence."""

    t = np.arange(int(WORD_S * rate)) / rate
    word = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    quiet = np.zeros_like(word)
    return b"".join((word if item is not None else quiet).tobytes() for item in layout)


def stub_words(audio: np.ndarray) -> Transcription:
    """A word for every loud stretch of 16 kHz audio, named by its order."""

    frame = int(FRAME * MODEL_RATE)
    frames = len(audio) // frame
    loud = [float(np.abs(audio[i * frame:(i + 1) * frame]).mean()) > 0.05 for i in range(frames)]
    words, start = [], None
    for index, is_loud in enumerate([*loud, False]):
        if is_loud and start is None:
            start = index
        elif not is_loud and start is not None:
            words.append(Word(f"w{len(words)}", round(start * FRAME, 3),
                              round(index * FRAME, 3), 0.9))
            start = None
    return Transcription(" ".join(w.word for w in words), tuple(words))


class StubVad(ChunkVad):
    """Rates a chunk as speech when it is loud; counts the chunks it rated."""

    ready = True

    def __init__(self, deaf: bool = False) -> None:
        self.chunks = 0
        self.deaf = deaf

    def initial_state(self, batch: int) -> None:
        return None

    def step(self, chunks: np.ndarray, state: None) -> tuple[np.ndarray, None]:
        self.chunks += len(chunks)
        loud = np.abs(chunks[:, CONTEXT:]).mean(axis=1) > (2.0 if self.deaf else 0.05)
        return loud.astype(np.float32) * 0.9 + 0.05, state


class StubEngine:
    ready = True

    def __init__(self) -> None:
        self.clips: list[int] = []

    async def transcribe(self, clip: np.ndarray) -> Transcription:
        assert len(clip) <= 30 * MODEL_RATE, "clip longer than the model accepts"
        self.clips.append(len(clip))
        return stub_words(clip)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def wav_bytes(pcm: bytes, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


async def run_session(session: RealtimeSession, pcm: bytes, rate: int, chunk_s: float = 0.08):
    step = int(rate * chunk_s) * 2
    events = []
    for offset in range(0, len(pcm), step):
        events += await session.feed(pcm[offset:offset + step])
    events += await session.commit()
    return events


class CtcWordsTest(unittest.TestCase):
    def test_pieces_with_word_marker_start_words_and_runs_give_times(self) -> None:
        pieces = ["▁xin", "▁ch", "ào", "▁bạn"]
        # blank, xin xin, blank, ch, ào ào, blank, bạn
        best = np.array([0, 2, 2, 0, 3, 4, 4, 0, 5])
        conf = np.array([0.9, 0.8, 0.6, 0.9, 0.5, 0.7, 0.9, 0.9, 1.0])
        decode = lambda units: "".join(pieces[u - 2] for u in units).replace("▁", " ")  # noqa: E731
        result = ctc_words(best, conf, pieces, decode, frame_s=0.04)
        self.assertEqual(result.text, "xin chào bạn")
        self.assertEqual([(w.word, w.start, w.end) for w in result.words],
                         [("xin", 0.04, 0.12), ("chào", 0.16, 0.28), ("bạn", 0.32, 0.36)])
        self.assertAlmostEqual(result.words[0].confidence, 0.7)
        self.assertAlmostEqual(result.words[1].confidence, 0.7)

    def test_repeated_unit_after_blank_is_a_new_emission(self) -> None:
        pieces = ["▁a"]
        result = ctc_words(np.array([2, 0, 2, 1]), np.ones(4), pieces,
                           lambda units: " ".join("a" for _ in units))
        self.assertEqual(result.text, "a a")


    def test_words_in_the_end_padding_are_dropped(self) -> None:
        result = Transcription("a b", (Word("a", 0.5, 0.54, 0.9), Word("b", 2.96, 3.0, 0.9)))
        self.assertEqual(before(result, 2.96).text, "a")
        self.assertIs(before(result, 3.5), result)


class SplitTest(unittest.TestCase):
    def test_long_audio_is_cut_in_the_quietest_place_after_twenty_seconds(self) -> None:
        audio = np.full(70 * MODEL_RATE, 0.3, dtype=np.float32)
        audio[int(24.5 * MODEL_RATE):int(25 * MODEL_RATE)] = 0.0
        pieces = split_long(audio)
        self.assertTrue(all(len(clip) <= 30 * MODEL_RATE for _, clip in pieces))
        self.assertEqual(sum(len(clip) for _, clip in pieces), len(audio))
        self.assertTrue(24.5 <= pieces[1][0] / MODEL_RATE <= 25.0)
        self.assertEqual(split_long(audio[:MODEL_RATE])[0][0], 0)


class VadTest(unittest.TestCase):
    def test_hysteresis_keeps_short_dips_and_ends_after_the_minimum_silence(self) -> None:
        tracker = RegionTracker(threshold=0.5, min_silence_s=0.1)
        probs = [0.1, 0.6, 0.4, 0.2, 0.7, 0.2, 0.2, 0.2, 0.2, 0.9]
        edges = [tracker.push(p, i * 0.032) for i, p in enumerate(probs)]
        tracker.close()
        self.assertEqual(edges[1], "started")
        self.assertEqual(edges.count("stopped"), 1)
        self.assertEqual(tracker.regions[0], Region(0.032, 0.16))
        self.assertEqual(len(tracker.regions), 2)

    def test_state_is_reset_after_a_second_without_speech(self) -> None:
        vad = StubVad()
        resets = []
        vad.initial_state = lambda batch: resets.append(batch)  # type: ignore[method-assign]
        stream = VadStream(vad, 16_000)
        stream.push(np.zeros(int(2.5 * MODEL_RATE), dtype=np.float32))
        self.assertEqual(len(resets), 3)  # the initial state, then after 1 s and 2 s

    def test_stream_resampling_matches_whole_audio(self) -> None:
        for rate in (8_000, 44_100, 48_000):
            t = np.arange(rate * 2) / rate
            audio = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
            resampler = StreamResampler(rate)
            step = int(rate * 0.08)
            streamed = np.concatenate([resampler.push(audio[i:i + step])
                                       for i in range(0, len(audio), step)])
            whole = resample(audio, rate)
            inner = slice(MODEL_RATE // 10, len(streamed) - MODEL_RATE // 10)
            self.assertGreater(len(streamed), len(whole) - MODEL_RATE // 20)
            self.assertLess(np.abs(streamed[inner] - whole[inner]).max(), 1e-3, rate)

class RealtimeSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_pause_ends_an_utterance_with_session_times(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig(endpointing_ms=800))
        # w w _ w | 4 x 0.3 s silence | w w | silence
        pcm = tone_pcm([1, None, 1, None, 1, None, None, None, None, 1, None, 1, None, None])
        events = await run_session(session, pcm, 16_000)
        finals = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertEqual(len(finals), 2)
        self.assertEqual(len(finals[0]["words"]), 3)
        self.assertEqual(len(finals[1]["words"]), 2)
        self.assertAlmostEqual(finals[0]["words"][0]["start"], 0.0, places=2)
        self.assertAlmostEqual(finals[1]["words"][0]["start"], 2.7, delta=0.03)
        self.assertAlmostEqual(finals[1]["words"][1]["end"], 3.6, delta=0.03)
        self.assertEqual(events[-1]["type"], "input_audio_buffer.committed")
        self.assertAlmostEqual(events[-1]["audio_processed"], 4.2, places=2)

    async def test_partials_only_append_settled_words(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig(endpointing_ms=800))
        pcm = tone_pcm([1, None] * 6)
        events = await run_session(session, pcm, 16_000)
        deltas = "".join(e["delta"] for e in events if e["type"] == EVENT_PREFIX + "delta")
        final = next(e for e in events if e["type"] == EVENT_PREFIX + "completed")
        self.assertTrue(deltas)
        self.assertTrue(final["transcript"].startswith(deltas))

    async def test_other_sample_rates_keep_times(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig(sample_rate=48_000))
        events = await run_session(session, tone_pcm([None, 1, None], 48_000), 48_000)
        (final,) = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertAlmostEqual(final["words"][0]["start"], 0.3, delta=0.03)

    async def test_long_speech_is_cut_below_thirty_seconds(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig())
        pcm = tone_pcm([1, None] * 70)  # 42 s without a long pause
        events = await run_session(session, pcm, 16_000, chunk_s=0.2)
        finals = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertGreaterEqual(len(finals), 2)
        self.assertEqual(sum(len(f["words"]) for f in finals), 70)
        starts = [w["start"] for f in finals for w in f["words"]]
        self.assertEqual(starts, sorted(starts))
        self.assertLessEqual(max(engine.clips), 30 * MODEL_RATE)

    async def test_silence_gives_no_events_and_a_bounded_buffer(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig())
        events = await run_session(session, tone_pcm([None] * 40), 16_000)
        self.assertEqual([e["type"] for e in events], ["input_audio_buffer.committed"])
        self.assertLess(max(engine.clips), 2 * MODEL_RATE)

    async def test_vad_runs_the_model_rarely_on_silence(self) -> None:
        engine, vad = StubEngine(), StubVad()
        session = RealtimeSession(engine.transcribe, SessionConfig(), vad=vad)
        events = await run_session(session, tone_pcm([None] * 40), 16_000)  # 12 s
        self.assertEqual([e["type"] for e in events], ["input_audio_buffer.committed"])
        self.assertLessEqual(len(engine.clips), 7)  # every 2 s and at commit, not every 0.4 s
        self.assertGreater(vad.chunks, 300)

    async def test_vad_reports_speech_edges_and_keeps_every_word(self) -> None:
        engine = StubEngine()
        pcm = tone_pcm([None, 1, None, 1] + [None] * 8 + [1, None, None, None])
        session = RealtimeSession(engine.transcribe, SessionConfig(), vad=StubVad())
        events = await run_session(session, pcm, 16_000)
        finals = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertEqual([f["transcript"] for f in finals], ["w0 w1", "w0"])
        # The 0.3 s pause between the first two words stays inside one region.
        kinds = [e["type"] for e in events if e["type"].startswith("input_audio_buffer.speech")]
        self.assertEqual(kinds, ["input_audio_buffer.speech_started",
                                 "input_audio_buffer.speech_stopped"] * 2)
        started = [e["audio_start_ms"] for e in events if "audio_start_ms" in e]
        stopped = [e["audio_end_ms"] for e in events if "audio_end_ms" in e]
        self.assertAlmostEqual(started[0], 300, delta=40)
        self.assertAlmostEqual(stopped[0], 1200, delta=40)
        self.assertAlmostEqual(started[1], 3600, delta=40)

    async def test_speech_the_vad_misses_is_still_recognized(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig(), vad=StubVad(deaf=True))
        pcm = tone_pcm([None] * 8 + [1, None, 1] + [None] * 8)
        events = await run_session(session, pcm, 16_000)
        finals = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertEqual([f["transcript"] for f in finals], ["w0 w1"])
        self.assertFalse([e for e in events if "speech_started" in e["type"]])

    async def test_vad_pause_sends_the_final_without_waiting_for_the_interval(self) -> None:
        engine = StubEngine()
        session = RealtimeSession(engine.transcribe, SessionConfig(endpointing_ms=800),
                                  vad=StubVad(), partial_interval_s=2.0)
        events = await run_session(session, tone_pcm([1] + [None] * 10), 16_000, chunk_s=0.04)
        final = next(e for e in events if e["type"] == EVENT_PREFIX + "completed")
        self.assertEqual(final["transcript"], "w0")
        self.assertLessEqual(final["audio_processed"], 0.3 + 0.8 + 0.12)

    def test_diarization_and_bad_values_are_refused(self) -> None:
        config = SessionConfig()
        with self.assertRaises(ValueError):
            config.update({"speaker_diarization": True})
        with self.assertRaises(ValueError):
            config.update({"sample_rate": 4000})
        config.update({"speaker_diarization": False, "endpointing_ms": 600})
        self.assertEqual(config.endpointing_ms, 600)
        with self.assertRaises(ValueError):
            config.update({"vad_threshold": 0.95})
        config.update({"vad": False, "vad_threshold": 0.3})
        self.assertEqual((config.vad, config.vad_threshold), (False, 0.3))
        without = SessionConfig(vad=False, vad_available=False)
        with self.assertRaises(ValueError):
            without.update({"vad": True})


class AppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = StubEngine()
        self.client = TestClient(create_app(self.engine, Settings(), StubVad()))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_ready_and_models(self) -> None:
        ready = self.client.get("/ready").json()
        self.assertEqual(ready["status"], "ready")
        self.assertTrue(ready["vad"].startswith("silero-vad"))
        self.assertEqual(self.client.get("/v1/models").json()["data"][0]["id"],
                         "5CD-AI/VieGem-ASR")

    def test_verbose_json_has_words_and_long_files_are_split(self) -> None:
        pcm = tone_pcm([1, None] * 60)  # 36 s
        response = self.client.post(
            "/v1/audio/transcriptions",
            data={"response_format": "verbose_json", "language": "vi-VN"},
            files={"file": ("a.wav", wav_bytes(pcm), "audio/wav")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["words"]), 60)
        self.assertEqual(body["language"], "vi-VN")
        self.assertAlmostEqual(body["duration"], 36.0, places=2)
        self.assertEqual(len(self.engine.clips), 2)
        self.assertEqual([w["start"] for w in body["words"]],
                         sorted(w["start"] for w in body["words"]))

    def test_vad_can_be_disabled_on_the_server(self) -> None:
        with TestClient(create_app(StubEngine(), Settings(vad=False))) as client:
            self.assertIsNone(client.get("/ready").json()["vad"])
            with client.websocket_connect("/v1/audio/transcriptions/realtime") as socket:
                self.assertFalse(socket.receive_json()["session"]["vad"])
                socket.send_text(json.dumps({"type": "session.update", "session": {"vad": True}}))
                self.assertEqual(socket.receive_json()["type"], "error")

    def test_simple_transcribe_and_errors(self) -> None:
        wav = wav_bytes(tone_pcm([1, None, 1]))
        self.assertEqual(self.client.post("/transcribe", files={"file": ("a.wav", wav)}).json(),
                         {"text": "w0 w1"})
        refused = self.client.post("/v1/audio/transcriptions", data={"diarization": "true"},
                                   files={"file": ("a.wav", wav)})
        self.assertEqual(refused.status_code, 400)
        broken = self.client.post("/transcribe", files={"file": ("a.wav", b"not audio")})
        self.assertEqual(broken.status_code, 400)

    def test_realtime_protocol(self) -> None:
        with self.client.websocket_connect("/v1/audio/transcriptions/realtime") as socket:
            self.assertEqual(socket.receive_json()["type"], "session.created")
            socket.send_text(json.dumps({"type": "session.update", "session": {
                "sample_rate": 16000, "language": "vi-VN", "word_timestamps": True,
                "endpointing_ms": 800}}))
            self.assertEqual(socket.receive_json()["type"], "session.updated")
            pcm = tone_pcm([1, None, 1, None, None])
            for offset in range(0, len(pcm), 2560):
                socket.send_bytes(pcm[offset:offset + 2560])
            socket.send_text(json.dumps({"type": "input_audio_buffer.commit"}))
            events = []
            while not events or events[-1]["type"] != "input_audio_buffer.committed":
                events.append(socket.receive_json())
        finals = [e for e in events if e["type"] == EVENT_PREFIX + "completed"]
        self.assertEqual([f["transcript"] for f in finals], ["w0 w1"])

    def test_realtime_refuses_diarization(self) -> None:
        with self.client.websocket_connect("/v1/audio/transcriptions/realtime") as socket:
            socket.receive_json()
            socket.send_text(json.dumps({"type": "session.update",
                                         "session": {"speaker_diarization": True}}))
            self.assertEqual(socket.receive_json()["type"], "error")


if __name__ == "__main__":
    unittest.main()
