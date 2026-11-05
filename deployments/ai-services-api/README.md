# AI services API

`ai-services-api` is the single entry point for all self-hosted AI services:
one FastAPI app ([`ai_services_api/`](ai_services_api/)) on one port
(`127.0.0.1:${AI_SERVICES_API_PORT:-8080}`, `http://ai-services-api:8080` inside
Compose). It runs no models. Each request, HTTP or WebSocket, is forwarded
to the worker container that owns its path, as listed in
[`services.yaml`](services.yaml).

**Endpoint reference:** [AI_SERVICES_API_DOCS.md](AI_SERVICES_API_DOCS.md)
(request fields, responses, the realtime WebSocket protocol, errors and
examples). Interactive docs: `http://127.0.0.1:8080/docs`.

```text
client ──► ai-services-api :8080 ──┬─► ai-speech     /v1/audio/transcriptions[/realtime]
                                   ├─► ai-ocr        /v1/ocr ──► ai-ocr-vlm (vLLM)
                                   ├─► ai-embedding  /v1/embeddings[/tokenize, /info]
                                   └─► ai-llm        /v1/chat/completions, /v1/completions, ...
```

| Service | Paths | Worker containers | Guide |
| --- | --- | --- | --- |
| `speech` | `POST /v1/audio/transcriptions`, `WS /v1/audio/transcriptions/realtime` | `ai-speech` | [speech](../speech/README.md) |
| `ocr` | `POST /v1/ocr` | `ai-ocr`, `ai-ocr-vlm` | [OCR](../ocr/README.md) |
| `embedding` | `POST /v1/embeddings` (OpenAI-compatible), `POST /v1/embeddings/tokenize`, `GET /v1/embeddings/info` | `ai-embedding` (TEI) | [embedding](../embedding/README.md) |
| `llm` | vLLM's own paths: `POST /v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages`, `/tokenize`, `/detokenize` | `ai-llm` (vLLM, Qwen/Qwen3.5-4B; also direct on port 8043) | [LLM](../llm/README.md) |

The models run in separate workers because their runtimes cannot share one
Python process: VieGem-ASR needs Python 3.12, PyTorch 2.14 and cuDNN 9.10, while
PaddleOCR-VL needs Python 3.10, PaddlePaddle 3.2.1 and cuDNN 9.5. The OCR model
itself also runs in a vLLM server. Workers publish no host ports, except
`ai-embedding`, which keeps port 8001 for clients that used the earlier
`tei-embeddings` container.

## Run

From the repository root (speech needs `bash deployments/speech/setup.sh` once):

```sh
bash deployments/ai-services-api/up.sh               # gateway and every service
bash deployments/ai-services-api/up.sh speech        # gateway and speech only
bash deployments/ai-services-api/up.sh embedding     # gateway and embeddings only
bash deployments/ai-services-api/up.sh --no-build ocr
curl -s http://127.0.0.1:8080/v1/services
```

`up.sh` starts `ai-services-api` and the worker `ai-<name>` of each named
service, then waits until each one is ready behind the gateway. Compose
equivalent:

```sh
docker compose --env-file .env -f deployments/docker-compose.yml \
  -f deployments/ai.compose.yml up -d --build ai-services-api ai-speech ai-ocr ai-embedding
```

A service whose worker is not running answers 503 on its paths. The other
services are unaffected.

## Gateway endpoints

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok"}` while the gateway runs |
| GET | `/ready` | 200 when every enabled service is ready, else 503; `services.<name>` has `ready`, the worker's `detail` and any connection `error` |
| GET | `/v1/services` | Each service's name, description, routes and readiness |
| GET | `/v1/services/{name}/ready` | The worker's own `/ready` body and status; 503 `{"status": "unavailable"}` if unreachable, 404 for an unknown name |
| GET | `/v1/models` | All workers' models, each with `service`; `unavailable_services` lists the workers that did not answer |
| GET | `/docs`, `/redoc`, `/openapi.json` | Interactive docs and OpenAPI document of the gateway and every service's public routes |
| any | a service route | Forwarded to that service |
| any | anything else | 404 `{"detail": "no AI service serves <path>"}` |

Forwarding behavior:

- **HTTP:** request and response bodies are streamed; status codes, headers
  and error bodies pass through. Responses carry `x-ai-service: <name>`.
  Errors: 503 if the worker cannot be reached, 504 if it does not answer
  within `AI_REQUEST_TIMEOUT_S`, 502 for other transport failures.
- **WebSocket:** text and binary messages are relayed both ways. When one side
  closes, the other is closed with the same code. If the worker cannot be
  reached, the client is closed with code 1013 (try again later).
- **Paths:** a route is forwarded to the same worker path, or to the worker
  path it maps to in `services.yaml` (`/v1/embeddings/tokenize` to TEI's
  `/tokenize`). Worker paths outside the routes (`/health`, `/transcribe`,
  TEI's `/embed`) are not reachable through the gateway.
- **Docs:** `/openapi.json` merges the gateway's endpoints with each worker's
  OpenAPI document (the entry's `openapi` path), keeping only operations under
  public routes, at their public paths, tagged by service. A worker that is
  down keeps its last fetched operations; one never reached is named in the
  description. WebSocket endpoints cannot be described in OpenAPI; see
  [AI_SERVICES_API_DOCS.md](AI_SERVICES_API_DOCS.md).

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `AI_SERVICES_API_PORT` | `8080` | Host port (bound to `127.0.0.1`) |
| `AI_SERVICES` | empty (all) | Comma-separated names of the enabled services |
| `AI_SERVICE_<NAME>_URL` | from `services.yaml` | Overrides a worker URL, e.g. `AI_SERVICE_OCR_URL` |
| `AI_REQUEST_TIMEOUT_S` | `900` | Longest a worker may take for one request |
| `AI_READY_TIMEOUT_S` | `5` | Timeout of each readiness probe |
| `AI_SERVICES_CONFIG` | `/app/services.yaml` | Registry file |

## Adding a service

1. Write the worker as an HTTP server that answers `GET /ready` (200 only when
   it can serve) and `GET /v1/models` (`{"object": "list", "data": [...]}`).
   For an existing server, set the entry's `ready`, `models` and `openapi`
   paths instead (the embedding entry uses TEI's `/health`, `/info` and
   `/api-doc/openapi.json`).
2. Add it to [`../ai.compose.yml`](../ai.compose.yml) as `ai-<name>`, without
   `ports`, with a healthcheck.
3. Add an entry to [`services.yaml`](services.yaml) with its `url`
   (`http://ai-<name>:<port>`) and `routes`. Routes may not repeat another
   service's route or cover the gateway's own paths; the gateway refuses to
   start otherwise.
4. Rebuild the gateway (`up.sh <name>`), since the registry is part of its
   image, or mount a changed file at `AI_SERVICES_CONFIG`.

## Tests

```sh
# Registry and forwarding against fake workers (no GPU or models):
uv run python -m unittest discover -s deployments/ai-services-api/tests -t deployments/ai-services-api -v
# Through the running gateway:
bash deployments/speech/smoke_test.sh [speech.wav ["reference transcript"]]
bash deployments/ocr/smoke_test.sh [document.pdf]
bash deployments/embedding/smoke_test.sh
```

Measured through the gateway: the speech smoke test and the backend's live
speech tests passed. A 162 s realtime stream of 12 FLEURS clips gave the same
WER as the worker alone (5.93%) and took 23.4 s instead of 21.5 s. The OCR
smoke test passed on a 2-page PDF. Embeddings through the gateway matched those
of the direct port (cosine 0.999999).

## Limitations

- No authentication or TLS; keep the port on `127.0.0.1` or put an
  authenticating proxy in front.
- No per-client rate limits; each worker applies its own limits (OCR queues
  one document at a time).
