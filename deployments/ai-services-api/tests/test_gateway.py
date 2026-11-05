"""Tests of ai-services-api against small fake workers run with uvicorn.

The fake workers are real HTTP/WebSocket servers on local ports, so the
gateway's streaming, header handling, WebSocket relay and readiness reports
are checked end to end without GPUs or models.

    uv run python -m unittest discover -s deployments/ai-services-api/tests -t deployments/ai-services-api -v
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_services_api.app import Settings, create_app
from ai_services_api.openapi import merge
from ai_services_api.registry import ConfigError, Service, find, load_services


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(app: FastAPI) -> tuple[str, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("fake worker did not start")
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}", server


def speech_worker() -> FastAPI:
    app = FastAPI()

    @app.get("/ready")
    async def ready():
        return {"status": "ready", "model": "fake-asr"}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "fake-asr"}]}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request, file: UploadFile = File(...),
                             language: str = Form("vi-VN")):
        data = await file.read()
        if language == "bad":
            return JSONResponse({"detail": "bad language"}, status_code=400)
        return {"bytes": len(data), "language": language, "query": request.url.query,
                "trace": request.headers.get("x-trace")}

    @app.websocket("/v1/audio/transcriptions/realtime")
    async def realtime(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "session.created",
                                              "query": websocket.url.query}))
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await websocket.send_bytes(len(message["bytes"]).to_bytes(4, "big"))
                elif message.get("text") == "close":
                    await websocket.close(4000, "done")
                    return
                else:
                    await websocket.send_text(message["text"].upper())
        except WebSocketDisconnect:
            return

    return app


def tei_worker() -> FastAPI:
    """Text Embeddings Inference's shape: /health and /info, no /ready or /v1/models."""

    app = FastAPI()

    @app.get("/health")
    async def health():
        return JSONResponse(None)

    @app.get("/info")
    async def info():
        return {"model_id": "microsoft/harrier-oss-v1-0.6b", "model_dtype": "float16",
                "max_input_length": 8192, "max_client_batch_size": 32, "docker_label": "x"}

    @app.post("/tokenize")
    async def tokenize(request: Request):
        body = await request.json()
        return [[{"id": 1, "text": body["inputs"], "special": False, "start": 0, "stop": 3}]]

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        return {"object": "list", "model": body.get("model"),
                "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2]}
                         for i, _ in enumerate(body["input"])]}

    return app


def ocr_worker_loading() -> FastAPI:
    app = FastAPI()

    @app.get("/ready")
    async def ready():
        return JSONResponse({"status": "loading"}, status_code=503)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "fake-ocr"}]}

    return app


class RegistryTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        folder = tempfile.mkdtemp()
        path = Path(folder) / "services.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_repository_registry_loads(self) -> None:
        path = Path(__file__).resolve().parents[1] / "services.yaml"
        services = load_services(path, {})
        self.assertEqual([s.name for s in services], ["speech", "ocr", "embedding", "llm"])
        embedding = services[2]
        self.assertEqual((embedding.ready_path, embedding.models_path), ("/health", "/info"))
        self.assertEqual(find(services, "/v1/embeddings").name, "embedding")
        self.assertEqual(embedding.target("/v1/embeddings"), "/v1/embeddings")
        self.assertEqual(embedding.target("/v1/embeddings/tokenize"), "/tokenize")
        self.assertEqual(embedding.target("/v1/embeddings/info"), "/info")
        self.assertEqual(embedding.openapi_path, "/api-doc/openapi.json")
        speech = services[0]
        self.assertEqual(speech.target("/v1/audio/transcriptions/realtime"),
                         "/v1/audio/transcriptions/realtime")
        # Worker-native paths are not public.
        for path in ("/embed", "/info", "/transcribe", "/health"):
            self.assertIsNone(find(services, path), path)
        self.assertEqual(find(services, "/tokenize").name, "llm")  # vLLM-native path
        self.assertEqual(find(services, "/v1/audio/transcriptions/realtime").name, "speech")
        self.assertEqual(find(services, "/v1/ocr").name, "ocr")
        self.assertIsNone(find(services, "/v1/ocrx"))
        self.assertIsNone(find(services, "/ready"))

    def test_environment_selects_services_and_overrides_urls(self) -> None:
        path = Path(__file__).resolve().parents[1] / "services.yaml"
        services = load_services(path, {"AI_SERVICES": "ocr",
                                        "AI_SERVICE_OCR_URL": "http://localhost:9000/"})
        self.assertEqual([(s.name, s.url) for s in services], [("ocr", "http://localhost:9000")])
        with self.assertRaises(ConfigError):
            load_services(path, {"AI_SERVICES": "speech,tts"})

    def test_invalid_registries_are_refused(self) -> None:
        bad = {
            "duplicate route": "services:\n  a: {url: 'http://a', routes: [/v1/x]}\n"
                               "  b: {url: 'http://b', routes: [/v1/x]}\n",
            "gateway path": "services:\n  a: {url: 'http://a', routes: [/v1/models]}\n",
            "covers gateway": "services:\n  a: {url: 'http://a', routes: [/v1]}\n",
            "bad url": "services:\n  a: {url: 'a:80', routes: [/v1/x]}\n",
            "bad name": "services:\n  A-b: {url: 'http://a', routes: [/v1/x]}\n",
            "no routes": "services:\n  a: {url: 'http://a'}\n",
            "bad probe": "services:\n  a: {url: 'http://a', routes: [/v1/x], ready: health}\n",
            "bad rewrite": "services:\n  a: {url: 'http://a', routes: [{/v1/x: info}]}\n",
        }
        for case, text in bad.items():
            with self.subTest(case), self.assertRaises(ConfigError):
                load_services(self.write(text), {})

    def test_longest_route_wins(self) -> None:
        services = [Service("a", "http://a", ("/v1/audio",)),
                    Service("b", "http://b", ("/v1/audio/speech",))]
        self.assertEqual(find(services, "/v1/audio/speech/x").name, "b")
        self.assertEqual(find(services, "/v1/audio/transcriptions").name, "a")


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.servers = []
        speech_url, server = serve(speech_worker())
        cls.servers.append(server)
        ocr_url, server = serve(ocr_worker_loading())
        cls.servers.append(server)
        tei_url, server = serve(tei_worker())
        cls.servers.append(server)
        cls.services = [
            Service("speech", speech_url, ("/v1/audio/transcriptions",)),
            Service("ocr", ocr_url, ("/v1/ocr",)),
            Service("embedding", tei_url,
                    ("/v1/embeddings", "/v1/embeddings/tokenize", "/v1/embeddings/info"),
                    ready_path="/health", models_path="/info",
                    rewrites={"/v1/embeddings/tokenize": "/tokenize",
                              "/v1/embeddings/info": "/info"}),
            Service("tts", f"http://127.0.0.1:{free_port()}", ("/v1/audio/speech",)),
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        for server in cls.servers:
            server.should_exit = True

    def setUp(self) -> None:
        self.client = TestClient(create_app(self.services, Settings(ready_timeout_s=2)))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_uploads_stream_through_with_query_headers_and_status(self) -> None:
        payload = b"x" * (5 * 1024 * 1024)
        response = self.client.post("/v1/audio/transcriptions?debug=1",
                                    files={"file": ("a.wav", payload)},
                                    data={"language": "en"}, headers={"x-trace": "abc"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"bytes": len(payload), "language": "en",
                                           "query": "debug=1", "trace": "abc"})
        self.assertEqual(response.headers["x-ai-service"], "speech")
        refused = self.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"1")},
                                   data={"language": "bad"})
        self.assertEqual((refused.status_code, refused.json()), (400, {"detail": "bad language"}))

    def test_websocket_messages_and_close_codes_are_relayed(self) -> None:
        with self.client.websocket_connect("/v1/audio/transcriptions/realtime?rate=16000") as ws:
            self.assertEqual(ws.receive_json(), {"type": "session.created",
                                                 "query": "rate=16000"})
            ws.send_bytes(b"\0" * 2560)
            self.assertEqual(int.from_bytes(ws.receive_bytes(), "big"), 2560)
            ws.send_text("commit")
            self.assertEqual(ws.receive_text(), "COMMIT")
            ws.send_text("close")
            with self.assertRaises(WebSocketDisconnect) as closed:
                ws.receive_text()
        self.assertEqual(closed.exception.code, 4000)

    def test_openai_embeddings_reach_the_tei_worker(self) -> None:
        response = self.client.post("/v1/embeddings", json={
            "model": "microsoft/harrier-oss-v1-0.6b", "input": ["xin chào", "trục Z"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-ai-service"], "embedding")
        self.assertEqual(len(response.json()["data"]), 2)
        tokens = self.client.post("/v1/embeddings/tokenize", json={"inputs": "abc"})
        self.assertEqual((tokens.status_code, tokens.json()[0][0]["text"]), (200, "abc"))
        info = self.client.get("/v1/embeddings/info").json()
        self.assertEqual(info["max_client_batch_size"], 32)
        self.assertEqual(self.client.post("/tokenize", json={"inputs": "abc"}).status_code, 404)

    def test_unreachable_and_unknown_targets(self) -> None:
        down = self.client.post("/v1/audio/speech", json={"text": "xin chào"})
        self.assertEqual(down.status_code, 503)
        self.assertIn("'tts' is unavailable", down.json()["detail"])
        with self.client.websocket_connect("/v1/audio/speech/stream") as ws:
            with self.assertRaises(WebSocketDisconnect) as closed:
                ws.receive_text()
        self.assertEqual(closed.exception.code, 1013)
        self.assertEqual(self.client.get("/v1/unknown").status_code, 404)

    def test_readiness_and_models_cover_every_service(self) -> None:
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503)
        services = ready.json()["services"]
        self.assertEqual({name: entry["ready"] for name, entry in services.items()},
                         {"speech": True, "ocr": False, "embedding": True, "tts": False})
        self.assertEqual(services["ocr"]["detail"], {"status": "loading"})
        self.assertIn("error", services["tts"])

        speech = self.client.get("/v1/services/speech/ready")
        self.assertEqual((speech.status_code, speech.json()["status"]), (200, "ready"))
        self.assertEqual(self.client.get("/v1/services/ocr/ready").status_code, 503)
        self.assertEqual(self.client.get("/v1/services/tts/ready").json()["status"], "unavailable")
        self.assertEqual(self.client.get("/v1/services/nope/ready").status_code, 404)

        listed = self.client.get("/v1/services").json()["services"]
        self.assertEqual([s["name"] for s in listed], ["speech", "ocr", "embedding", "tts"])
        self.assertEqual(listed[0]["routes"], ["/v1/audio/transcriptions"])

        models = self.client.get("/v1/models").json()
        self.assertEqual([(m["id"], m["service"]) for m in models["data"]],
                         [("fake-asr", "speech"), ("fake-ocr", "ocr"),
                          ("microsoft/harrier-oss-v1-0.6b", "embedding")])
        self.assertEqual(models["data"][2]["max_client_batch_size"], 32)
        self.assertNotIn("docker_label", models["data"][2])
        embedding = self.client.get("/v1/services/embedding/ready")
        self.assertEqual((embedding.status_code, embedding.json()), (200, {"status": "ready"}))
        self.assertEqual(models["unavailable_services"], ["tts"])

    def test_docs_list_the_public_routes_of_every_service(self) -> None:
        document = self.client.get("/openapi.json").json()
        paths = document["paths"]
        self.assertEqual(sorted(paths), [
            "/health", "/ready", "/v1/audio/transcriptions", "/v1/embeddings",
            "/v1/embeddings/info", "/v1/embeddings/tokenize", "/v1/models",
            "/v1/services", "/v1/services/{name}/ready"])
        transcribe = paths["/v1/audio/transcriptions"]["post"]
        self.assertEqual(transcribe["tags"], ["speech"])
        self.assertTrue(transcribe["operationId"].startswith("speech_"))
        schema = transcribe["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        self.assertIn(schema.rsplit("/", 1)[1], document["components"]["schemas"])
        self.assertEqual(paths["/v1/embeddings/tokenize"]["post"]["tags"], ["embedding"])
        self.assertEqual(paths["/ready"]["get"]["tags"], ["gateway"])
        self.assertIn("`tts`", document["info"]["description"])
        self.assertEqual([tag["name"] for tag in document["tags"]],
                         ["gateway", "speech", "ocr", "embedding", "tts"])
        self.assertIn("swagger-ui", self.client.get("/docs").text)
        self.assertEqual(self.client.get("/redoc").status_code, 200)

    def test_docs_rename_conflicting_components(self) -> None:
        def spec(path: str, field: str) -> dict:
            body = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Body"}}}}
            return {"paths": {path: {"post": {"operationId": "run", "requestBody": body}},
                              "/health": {"get": {}}},
                    "components": {"schemas": {
                        "Body": {"properties": {field: {"$ref": "#/components/schemas/Text"}}},
                        "Text": {"type": "string"}, "Unused": {"type": "integer"}}}}

        merged = merge({"paths": {}}, [
            (Service("a", "http://a", ("/v1/a",)), spec("/v1/a", "x")),
            (Service("b", "http://b", ("/v1/b",), rewrites={"/v1/b": "/run"}), spec("/run", "y"))])
        self.assertEqual(sorted(merged["paths"]), ["/v1/a", "/v1/b"])
        self.assertEqual(sorted(merged["components"]["schemas"]), ["Body", "Text", "b_Body"])
        ref = merged["paths"]["/v1/b"]["post"]["requestBody"]["content"]["application/json"]
        self.assertEqual(ref["schema"]["$ref"], "#/components/schemas/b_Body")
        self.assertEqual(merged["paths"]["/v1/b"]["post"]["operationId"], "b_run")


if __name__ == "__main__":
    unittest.main()
