# AI Services API reference

Endpoints of `ai-services-api`, the single HTTP and WebSocket API for the
self-hosted AI services: speech recognition (ASR), document OCR and text
embeddings. For deployment, see the [gateway guide](README.md).
Interactive docs (Swagger UI) with the same HTTP endpoints are at
`http://127.0.0.1:8080/docs`; WebSocket endpoints are described only here.

| | |
| --- | --- |
| Base URL (host) | `http://127.0.0.1:8080` (`AI_SERVICES_API_PORT`) |
| Base URL (inside Compose) | `http://ai-services-api:8080` |
| Authentication | None. The port is bound to `127.0.0.1`; put an authenticating proxy in front before exposing it |
| Request bodies | `multipart/form-data` for files (speech, OCR); JSON for embeddings |
| OpenAI compatibility | `/v1/audio/transcriptions` and `/v1/embeddings` work with the OpenAI SDKs (checked with `openai` 3.42.1 for Python) |

| Service | Model | Endpoints |
| --- | --- | --- |
| [Speech](#speech-recognition) | `5CD-AI/VieGem-ASR` (Vietnamese and Vietnamese–English), Silero VAD | `POST /v1/audio/transcriptions`, `WS /v1/audio/transcriptions/realtime` |
| [OCR](#document-ocr) | `PaddleOCR-VL-1.6-0.9B` with PP-DocLayoutV3 layout | `POST /v1/ocr` |
| [Embeddings](#text-embeddings) | `microsoft/harrier-oss-v1-0.6b` (1024 dimensions) | `POST /v1/embeddings`, `POST /v1/embeddings/tokenize`, `GET /v1/embeddings/info` |
| [Status](#status-endpoints) | | `GET /health`, `GET /ready`, `GET /v1/services`, `GET /v1/services/{name}/ready`, `GET /v1/models` |
| Docs | | `GET /docs` (Swagger UI), `GET /redoc`, `GET /openapi.json` |

```sh
curl -s http://127.0.0.1:8080/ready
curl -s -X POST http://127.0.0.1:8080/v1/audio/transcriptions -F file=@meeting.wav
curl -s -X POST http://127.0.0.1:8080/v1/ocr -F file=@manual.pdf -F response_format=markdown
curl -s http://127.0.0.1:8080/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input": ["Trục Z kêu rè khi chạy nhanh"]}'
```

## Status endpoints

| Method and path | Response |
| --- | --- |
| `GET /health` | `200 {"status": "ok"}` while the gateway runs; says nothing about the services |
| `GET /ready` | `200` when every enabled service is ready, else `503`. Body: `{"status": "ready" \| "not_ready", "services": {"<name>": {"ready": bool, "detail": {...}, "error": "..."}}}` |
| `GET /v1/services` | `{"services": [{"name", "description", "routes", "ready", "detail"}]}` |
| `GET /v1/services/{name}/ready` | The service's own readiness, with its status code: `200` ready, `503` loading or down (`{"status": "unavailable", "error": ...}` if unreachable), `404` unknown name. Names: `speech`, `ocr`, `embedding` |
| `GET /v1/models` | `{"object": "list", "data": [{"id", "object": "model", "service", ...}], "unavailable_services": [...]}` |

Check `GET /v1/services/{name}/ready` before using one service: `/ready` is
503 if any service is down, even when the one you need is ready.

```json
{"status": "ready",
 "services": {
   "speech": {"ready": true, "detail": {"status": "ready", "model": "5CD-AI/VieGem-ASR", "device": "cuda", "vad": "silero-vad 6.2.3"}},
   "ocr": {"ready": true, "detail": {"status": "ready", "pipeline": "PaddleOCR-VL-1.6", "vlm_model": "PaddleOCR-VL-1.6-0.9B", "doc_preprocessing": false}},
   "embedding": {"ready": true, "detail": {"status": "ready"}}}}
```

## Speech recognition

VieGem-ASR transcribes Vietnamese, including English terms. Output is lowercase
without punctuation, and numbers are written as words ("không phẩy không năm").
There is no speaker diarization and no translation. Word times come from the
CTC alignment: `start` is accurate to about 40 ms, and `end` is only one frame
after `start`.

### POST /v1/audio/transcriptions

Transcribes one audio file. Request: `multipart/form-data`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `file` | file | required | Audio in any format libsndfile reads (WAV, FLAC, OGG, …), any sample rate, mono or stereo (averaged). Not MP3 or M4A |
| `response_format` | string | `json` | `json`, `text` or `verbose_json` |
| `language` | string | `vi-VN` | Echoed in `verbose_json`; it does not change recognition |
| `model` | string | – | Accepted for OpenAI compatibility and ignored |
| `diarization` | bool | `false` | `true` is refused with 400 |

Other OpenAI fields (`prompt`, `temperature`, `timestamp_granularities[]`) are
ignored. Files over 30 s are cut at their quietest point between 20 and 30 s
and transcribed as one batch. Limits: 200 MB and one hour of audio.

Responses:

- `json`: `{"text": "người bệnh cũng đã từng tới nigeria nơi một số ca mắc bệnh ebola xảy ra"}`
- `text`: the text as `text/plain`.
- `verbose_json`:

```json
{
  "task": "transcribe",
  "model": "5CD-AI/VieGem-ASR",
  "language": "vi-VN",
  "duration": 8.82,
  "text": "người bệnh cũng đã từng tới nigeria nơi một số ca mắc bệnh ebola xảy ra",
  "words": [
    {"word": "người", "start": 1.6, "end": 1.64, "confidence": 0.9983},
    {"word": "bệnh", "start": 1.8, "end": 1.84, "confidence": 0.9966}
  ],
  "segments": [{"id": 0, "start": 1.6, "end": 7.36, "text": "người bệnh cũng đã ..."}]
}
```

`segments` holds one segment spanning all words (empty without words); use
`words` for timing. Errors: `400` unreadable file, bad `response_format` or
`diarization=true`; `413` file over 200 MB or one hour.

```sh
curl -s -X POST http://127.0.0.1:8080/v1/audio/transcriptions \
  -F file=@meeting.wav -F response_format=verbose_json
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="not-needed")
with open("meeting.wav", "rb") as audio:
    result = client.audio.transcriptions.create(
        model="5CD-AI/VieGem-ASR", file=audio, response_format="verbose_json")
print(result.text, result.words[0])
```

### WS /v1/audio/transcriptions/realtime

Live transcription of a PCM16 stream (for example a microphone). The server
re-transcribes the utterance in progress every 0.4 s of audio, sends the new
words as `delta` events, and sends a `completed` event when the speaker pauses
for `endpointing_ms`.

Session flow:

1. Connect to `ws://127.0.0.1:8080/v1/audio/transcriptions/realtime`. The server
   sends `session.created`.
2. Optionally send one `session.update` before any audio. The server answers
   `session.updated`.
3. Send audio as binary messages: mono, 16-bit little-endian PCM at the session's
   sample rate. Frames of 20–100 ms work well; send them at real-time pace.
4. Send `{"type": "input_audio_buffer.commit"}` after the last frame. The server
   finalizes the rest and ends with `input_audio_buffer.committed`.

`session.update` fields (all optional; unknown fields are ignored):

| Field | Default | Range | Meaning |
| --- | --- | --- | --- |
| `sample_rate` | `16000` | 8000–96000 | Rate of the binary PCM16 frames |
| `language` | `vi-VN` | | Echoed in `completed` |
| `endpointing_ms` | `800` (`SPEECH_EOU_MS`) | 200–5000 | Pause after the last word that ends an utterance |
| `word_timestamps` | `true` | | Include `words` in `completed` |
| `vad` | `true` | | Silero voice activity detection; it changes how often the model runs, never which words are kept |
| `vad_threshold` | `0.5` | 0.1–0.9 | Speech probability that starts speech |
| `speaker_diarization` | `false` | | `true` is refused with an `error` event |

```json
{"type": "session.update", "session": {"sample_rate": 16000, "endpointing_ms": 800}}
```

Server events:

| `type` | Fields | When |
| --- | --- | --- |
| `session.created`, `session.updated` | `session` (the settings above) | On connect; after `session.update` |
| `conversation.item.input_audio_transcription.delta` | `item_id`, `delta`, `audio_processed` | New settled words of the utterance in progress. Append `delta` to the text; it never revises earlier text |
| `conversation.item.input_audio_transcription.completed` | `item_id`, `transcript`, `language`, `audio_processed`, `words` | Final text of an utterance. It replaces that item's deltas and may correct them |
| `input_audio_buffer.speech_started` | `audio_start_ms`, `item_id` | The VAD heard speech start |
| `input_audio_buffer.speech_stopped` | `audio_end_ms`, `item_id` | The VAD heard speech stop |
| `input_audio_buffer.committed` | `audio_processed` | Last event after a commit |
| `error` | `error.message` | Invalid message or settings; the session stays open, except after an internal error (closed with code 1011) |

Times (`words[].start`, `audio_start_ms`, …) count from the session's first
audio frame; `audio_processed` is the seconds of audio received so far. The
last word of an utterance is held back from `delta` until it is settled, so
deltas run about one word behind. If the speech worker is unreachable, the
gateway closes the WebSocket with code 1013.

An utterance that reaches 25 s without a pause is cut at its longest pause
between words.

Example session, recorded with the Python client below (an 8.8 s recording
sent at real-time pace; the left column is when each event arrived):

```text
[0.00s] session.created
[0.00s] session.updated
[1.27s] input_audio_buffer.speech_started   audio_start_ms=1248
[2.08s] delta "người"
[2.49s] delta " bệnh cũng"
[2.89s] delta " đã từng"
[3.69s] delta " tới"
[4.19s] input_audio_buffer.speech_stopped   audio_end_ms=3872
[4.50s] completed "người bệnh cũng đã từng tới nigeria"   words[0]: {"word": "người", "start": 1.58, "end": 1.62, "confidence": 0.9987}
[4.84s] input_audio_buffer.speech_started   audio_start_ms=4800
[5.54s] delta "nơi"
  ...
[7.56s] delta " xảy"
[7.66s] input_audio_buffer.speech_stopped   audio_end_ms=7328
[8.61s] completed "nơi một số ca mắc bệnh ebola xảy ra"
[9.01s] input_audio_buffer.committed       audio_processed=8.82
```

The last words of each utterance ("nigeria", "ra") arrive only in `completed`.

```python
import asyncio, json, wave
import websockets

async def main():
    with wave.open("meeting.wav") as w:          # 16-bit mono WAV
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    url = "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv()                                         # session.created
        await ws.send(json.dumps({"type": "session.update", "session": {"sample_rate": rate}}))
        await ws.recv()                                         # session.updated

        async def send():
            step = rate * 80 // 1000 * 2                        # 80 ms frames
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
                await asyncio.sleep(0.08)                       # real-time pace
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        sender = asyncio.create_task(send())
        async for message in ws:
            event = json.loads(message)
            if event["type"].endswith(".completed"):
                print(event["transcript"])
            elif event["type"] in ("input_audio_buffer.committed", "error"):
                break
        await sender

asyncio.run(main())
```

The backend's `SpeechClient` (`backend/src/services/speech_processing.py`)
implements this protocol (`astream`) and the file endpoint.

## Document OCR

The PaddleOCR-VL-1.6 pipeline finds the layout regions of each page and their
reading order (PP-DocLayoutV3), then reads each region with the
PaddleOCR-VL-1.6-0.9B model. Tables come back as HTML and formulas as LaTeX.
Documents are processed one at a time; up to 4 more wait.

### POST /v1/ocr

Request: `multipart/form-data`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `file` | file | required | PDF (up to 100 pages), PNG or JPEG; the type is read from the content |
| `response_format` | string | `json` | `json`, or `markdown` for the document's Markdown only (`text/markdown`) |
| `doc_orientation` | bool | `false` | Detect 0/90/180/270° rotation and turn pages upright. Needs `OCR_DOC_PREPROCESSING=1` on the server, else 400 |
| `doc_unwarping` | bool | `false` | Flatten curved or skewed pages (UVDoc); same condition |
| `include_images` | bool | `false` | Add the figure crops the Markdown links to, as base64 JPEG |

JSON response:

```json
{
  "pipeline": "PaddleOCR-VL-1.6",
  "vlm_model": "PaddleOCR-VL-1.6-0.9B",
  "file_name": "manual.pdf",
  "file_type": "pdf",
  "page_count": 2,
  "settings": {"doc_orientation": false, "doc_unwarping": false},
  "elapsed_s": 1.25,
  "pages": [
    {
      "page_number": 1,
      "width": 1191,
      "height": 1684,
      "regions": [
        {"order": 3, "label": "paragraph_title", "bbox": [91, 321, 365, 365],
         "polygon": [[91.0, 321.0], [365.0, 321.0], [365.0, 365.0], [91.0, 365.0]],
         "content": "1. Bảng mã lỗi", "block_id": 2, "block_order": 3},
        {"order": 4, "label": "table", "bbox": [94, 389, 1096, 622], "polygon": [[94.0, 389.0], "..."],
         "content": "<table><tr><td>Mã lỗi</td><td>Mô tả</td>...</table>", "block_id": 3, "block_order": null}
      ],
      "markdown": "## ...\n\n### 1. Bảng mã lỗi\n\n<table ...>...</table>"
    }
  ],
  "markdown": "<!-- page 1 -->\n\n## ...\n\n<!-- page 2 -->\n\n### 3. Ghi chú an toàn\n..."
}
```

| Field | Meaning |
| --- | --- |
| `pages[]` | In document order; `page_number` starts at 1. An image is one page |
| `width`, `height` | Pixels of the page image the pipeline used. PDF pages are rendered at about 144 dpi (A4 is 1191×1684) |
| `regions[]` | Layout regions in reading order |
| `regions[].order` | Position in reading order, from 1 |
| `regions[].label` | `doc_title`, `paragraph_title`, `text`, `table` (HTML), `display_formula` (LaTeX), `image`, `figure_title`, `header`, `footer`, `number`, `footnote`, … |
| `regions[].bbox`, `polygon` | `[x1, y1, x2, y2]` and corner points in page pixels; after preprocessing, on the corrected page |
| `regions[].block_order` | PaddleOCR's own numbering; `null` for tables, images and some other labels |
| `pages[].preprocessing` | Only when preprocessing ran: `{"orientation_angle": 270, "unwarped": false}` |
| `pages[].markdown` | The page as Markdown, without headers, footers, page numbers, footnotes and side text |
| `pages[].markdown_images` | With `include_images=true`: `{"imgs/....jpg": "<base64 JPEG>"}` |
| `markdown` | All pages in order, each after `<!-- page N -->` |

Errors: `400` not a PDF/PNG/JPEG, unreadable PDF, bad `response_format`, or
preprocessing requested while it is off; `413` over 50 MB or 100 pages; `503`
too many documents waiting (retry later); `502` the OCR model server is down;
`500` the pipeline failed.

Timing on the deployment host: about 1 s per image page, 3.5 s for a 10-page
PDF, and about 12 s for the first request after a start.

```sh
curl -s -X POST http://127.0.0.1:8080/v1/ocr -F file=@manual.pdf > manual.json
curl -s -X POST http://127.0.0.1:8080/v1/ocr -F file=@manual.pdf -F response_format=markdown > manual.md
```

```python
import httpx

with open("manual.pdf", "rb") as document:
    response = httpx.post("http://127.0.0.1:8080/v1/ocr", files={"file": document},
                          data={"response_format": "json"}, timeout=600)
response.raise_for_status()
for page in response.json()["pages"]:
    for region in page["regions"]:
        print(page["page_number"], region["order"], region["label"], region["content"][:60])
```

## Text embeddings

`microsoft/harrier-oss-v1-0.6b` on Hugging Face Text Embeddings Inference
(TEI) 1.9: 1024-dimensional, normalized vectors (last-token pooling, float16).
Inputs longer than 8,192 tokens are truncated.

### POST /v1/embeddings

OpenAI-compatible. Request: JSON. The response below is for
`{"input": ["Trục Z kêu rè"]}`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `input` | string, list of strings, or token IDs | required | Up to 32 inputs per request |
| `model` | string | – | Optional. `microsoft/harrier-oss-v1-0.6b`; other names are ignored (the server logs a warning) |
| `encoding_format` | string | `float` | `float`, or `base64` (little-endian float32) |
| `dimensions` | integer | full (1024) | Keep only the first N values; quality at fewer dimensions has not been evaluated |
| `user` | string | – | Ignored |

```json
{
  "object": "list",
  "data": [{"object": "embedding", "index": 0, "embedding": [0.018502476, -0.09466528, 0.0, "... 1024 values"]}],
  "model": "microsoft/harrier-oss-v1-0.6b",
  "usage": {"prompt_tokens": 7, "total_tokens": 7}
}
```

Errors use TEI's format, `{"message": "...", "code": 422, "type": "Validation"}`:
`422` more than 32 inputs; `400` empty input.

```sh
curl -s http://127.0.0.1:8080/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model": "microsoft/harrier-oss-v1-0.6b", "input": ["Trục Z kêu rè", "grinding noise on the Z axis"]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="not-needed")
result = client.embeddings.create(model="microsoft/harrier-oss-v1-0.6b",
                                  input=["Trục Z kêu rè", "grinding noise on the Z axis"])
vectors = [item.embedding for item in result.data]   # 2 x 1024
```

Backend settings for this service: `EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1`,
`EMBEDDING_MODEL=microsoft/harrier-oss-v1-0.6b`, `EMBEDDING_DIM=1024`,
`EMBEDDING_BATCH_SIZE=32`, no `EMBEDDING_API_KEY`.

### POST /v1/embeddings/tokenize

Tokens of each input, for counting tokens before embedding (TEI `/tokenize`).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `inputs` | string or list of strings | required | Texts to tokenize |
| `add_special_tokens` | bool | `true` | Include the end-of-text token |

```sh
curl -s http://127.0.0.1:8080/v1/embeddings/tokenize -H 'Content-Type: application/json' \
  -d '{"inputs": "Trục Z kêu"}'
```

```json
[[{"id": 1282, "text": "Tr", "special": false, "start": 0, "stop": 2},
  {"id": 92704, "text": "ục", "special": false, "start": 2, "stop": 6},
  {"id": 1863, "text": " Z", "special": false, "start": 6, "stop": 8},
  {"id": 595, "text": " k", "special": false, "start": 8, "stop": 10},
  {"id": 124634, "text": "êu", "special": false, "start": 10, "stop": 13},
  {"id": 151643, "text": "<|endoftext|>", "special": true, "start": null, "stop": null}]]
```

`start` and `stop` are byte offsets in the UTF-8 text.

### GET /v1/embeddings/info

The model and the server's limits (TEI `/info`):

```json
{"model_id": "microsoft/harrier-oss-v1-0.6b", "model_dtype": "float16",
 "model_type": {"embedding": {"pooling": "last_token"}},
 "max_input_length": 8192, "max_batch_tokens": 8192, "max_client_batch_size": 32,
 "max_concurrent_requests": 256, "auto_truncate": true, "version": "1.9.3"}
```

## Errors

| Status | Source | Body |
| --- | --- | --- |
| `404` | Gateway: no service serves the path | `{"detail": "no AI service serves /path"}` |
| `503` | Gateway: the service's worker is not running | `{"detail": "AI service 'ocr' is unavailable: ConnectError"}` |
| `504` | Gateway: no answer within `AI_REQUEST_TIMEOUT_S` (900 s) | `{"detail": "AI service 'ocr' did not answer in time"}` |
| `502` | Gateway: other transport failure | `{"detail": "AI service '...' failed: ..."}` |
| `4xx`/`5xx` | Speech and OCR | `{"detail": "..."}` (FastAPI) |
| `4xx` | Embeddings | `{"message": "...", "code": 422, "type": "Validation"}` (TEI) |

Responses that come from a service carry the header `x-ai-service: speech`,
`ocr` or `embedding`.

## Not provided

- Speech: translation (`/v1/audio/translations`), text-to-speech
  (`/v1/audio/speech`), speaker diarization, punctuation and casing.
- OCR: Office documents, handwriting evaluation, merging of tables or
  paragraphs that continue across pages, input by URL or base64.
- Embeddings: reranking, sparse vectors, instruction prompts (`prompt_name`).
- Chat or text generation models.
- Authentication, per-client quotas and request IDs.

The workers also have native paths that the gateway does not expose (for
example TEI's `/embed`, `/decode` and the speech worker's `/transcribe`). The
embedding worker's own port 8001 still serves all TEI paths for existing
clients.
