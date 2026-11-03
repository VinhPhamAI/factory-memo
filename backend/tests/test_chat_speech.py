"""Checks for the chat composer's speech-to-text endpoint.

A controlled gateway serves the documented `/v1/audio/transcriptions` route
over real HTTP, so the real SpeechClient request path is exercised without a
speech model.
"""

from __future__ import annotations

import io
import threading
import time
import unittest
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.app import create_app
from backend.src.services.auth import Account, InvalidSession
from backend.src.services.speech_processing import SpeechClient, SpeechSettings


OWNER = Account(id=uuid4(), username="an", display_name="An", role="employee")
AUTH = {"Authorization": "Bearer owner-access"}


class StaticAuth:
    async def authenticate_access(self, token: str) -> Account:
        if token != "owner-access":
            raise InvalidSession()
        return OWNER


def wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """A mono PCM16 WAV with a loud tone, so it counts as speech."""
    import math

    frames = bytearray()
    for index in range(int(seconds * rate)):
        value = int(12000 * math.sin(2 * math.pi * 220 * index / rate))
        frames += value.to_bytes(2, "little", signed=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


class ControlledGateway:
    """Speech gateway stand-in that records uploads and can fail."""

    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.fail = False
        self.app = Starlette(
            routes=[Route("/v1/audio/transcriptions", self.transcribe, methods=["POST"])]
        )

    async def transcribe(self, request: Request) -> JSONResponse:
        form = await request.form()
        upload = form["file"]
        self.uploads.append(
            {"size": len(await upload.read()), "format": form.get("response_format")}
        )
        if self.fail:
            return JSONResponse({"error": {"message": "model not loaded at 10.0.0.9"}}, status_code=503)
        return JSONResponse({"text": "máy ép báo lỗi e mười hai", "duration": 1.0, "words": []})


@contextmanager
def serve(app: object) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class ChatSpeechTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = ControlledGateway()
        context = serve(self.gateway.app)
        url = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.client = self.app_client(SpeechClient(SpeechSettings(base_url=url, timeout_seconds=5)))

    def app_client(self, speech: object) -> TestClient:
        app = create_app(knowledge_agents={}, auth_service=StaticAuth(), chat_speech=speech)
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def post(self, content: bytes, headers: dict[str, str] | None = AUTH, name: str = "voice.wav"):
        return self.client.post(
            "/v1/speech/transcriptions",
            files={"file": (name, content, "audio/wav")},
            headers=headers or {},
        )

    def test_recording_returns_editable_text_through_the_gateway(self) -> None:
        response = self.post(wav())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "máy ép báo lỗi e mười hai"})
        self.assertEqual(len(self.gateway.uploads), 1)
        self.assertEqual(self.gateway.uploads[0]["format"], "verbose_json")

    def test_sign_in_is_required_before_the_gateway_is_called(self) -> None:
        for headers in (None, {"Authorization": "Bearer other"}):
            self.assertEqual(self.post(wav(), headers=headers).status_code, 401)
        self.assertEqual(self.gateway.uploads, [])

    def test_invalid_or_oversized_audio_is_rejected_before_the_gateway(self) -> None:
        self.assertEqual(self.post(b"not a wav file").status_code, 422)
        self.assertEqual(self.post(wav(rate=4000)).status_code, 422)
        self.assertEqual(self.post(wav(seconds=200)).status_code, 413)
        self.assertEqual(self.gateway.uploads, [])

    def test_gateway_failure_reaches_the_caller_without_details(self) -> None:
        self.gateway.fail = True
        response = self.post(wav())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("10.0.0.9", response.text)

    def test_unreachable_gateway_is_503(self) -> None:
        client = self.app_client(SpeechClient(SpeechSettings(base_url="http://127.0.0.1:1", timeout_seconds=2)))
        response = client.post(
            "/v1/speech/transcriptions", files={"file": ("v.wav", wav(), "audio/wav")}, headers=AUTH
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
