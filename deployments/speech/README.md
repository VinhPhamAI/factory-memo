# Speech service (NeMo-Speech.cpp)

`speech-api` in [`deployments/docker-compose.yml`](../docker-compose.yml) hosts
[NVIDIA NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp) `nemo-speech serve`
with:

- **ASR:** `nvidia/nemotron-3.5-asr-streaming-0.6b` (q8_0 GGUF), multilingual
  cache-aware streaming RNNT; `vi-VN` is a transcription-ready locale, `auto`
  detects the language.
- **Diarization:** `nvidia/Nemotron-3-Diarization` (q8_0 GGUF, Sortformer V3,
  up to 8 speakers) attaches 1-based speaker labels to ASR words.
- **Endpointing:** server-side end-of-utterance after `SPEECH_EOU_MS` (default
  800 ms) of trailing silence, so a stream yields one final per utterance.

The image is built from the pinned upstream commit with only CUDA + HTTP
enabled (no gRPC, KenLM/flashlight, ITN, NMT or VoiceChat). The port is bound to
`127.0.0.1` without an API key; put an authenticating TLS proxy in front before
exposing it beyond the host.

## Run

From the repository root (`.env` supplies `NGC_API_KEY`, optional `HF_TOKEN`
and the `SPEECH_*` settings in [`.env.example`](../../.env.example)):

```sh
bash deployments/speech/setup.sh       # source checkout, GGUF models, nvcr.io login
bash deployments/speech/up.sh          # build image + start + wait for /ready
bash deployments/speech/smoke_test.sh  # health, HTTP, diarization, realtime stream
bash deployments/speech/smoke_test.sh meeting.wav vi-VN  # also stream your own WAV
docker compose -f deployments/docker-compose.yml logs -f speech-api
```

The source is checked out at `deployments/NeMo-Speech.cpp/` and the models
are stored in `models/nemo-speech/`; both are ignored by Git. The first build
compiles the CUDA kernels; `SPEECH_CUDA_ARCH` (default `86`, A40) selects the
GPU architecture and `SPEECH_GPU_ID` the device.

## API

- `GET /health`, `GET /ready`, `GET /v1/models`; browser playground at `/`.
- `POST /v1/audio/transcriptions`: a complete WAV, `response_format=verbose_json`
  and `diarization=true` returns `words[]` with `start`, `end`, `confidence`
  and `speaker`.
- WebSocket `/v1/audio/transcriptions/realtime`: wait for `session.created`, send
  one `session.update` before any audio, then binary mono PCM16 frames, and finish with
  `{"type": "input_audio_buffer.commit"}`:

  ```json
  {"type": "session.update", "session": {"sample_rate": 16000, "language": "vi-VN",
   "word_timestamps": true, "speaker_diarization": true, "endpointing_ms": 800}}
  ```

  The server sends `conversation.item.input_audio_transcription.delta`
  (partial text), `.completed` (final `transcript` and `words` with speakers),
  `input_audio_buffer.committed` and `error`.

Upstream reference: `deployments/NeMo-Speech.cpp/docs/server.md` and `docs/api.md`.

## Python client

[`backend/src/services/speech_processing.py`](../../backend/src/services/speech_processing.py)
wraps both paths (`SpeechClient.atranscribe_file`, `SpeechClient.astream`) and
groups words into speaker turns. `format_transcript` renders them as
`Speaker N: text` lines, which `backend.scripts.kb ingest --file` reads;
`Utterance.to_json` keeps word timings and speaker labels as provenance.

```python
client = SpeechClient()  # SPEECH_API_URL, SPEECH_LANGUAGE, SPEECH_EOU_MS
turns = await client.atranscribe_file("meeting.wav", language="vi-VN")
pcm, rate = read_wav_pcm16("meeting.wav")
async for event in client.astream(apace(iter_pcm_chunks(pcm, rate), rate), sample_rate=rate):
    if event.kind == "completed":
        print(format_transcript(event.utterances))
```

```sh
SPEECH_API_URL_TEST=http://127.0.0.1:8080 uv run python -m unittest backend.tests.test_speech_processing -v
```

## Limitations

- Speaker numbers are per-recording diarization labels, not identities, and
  can change after long silences or overlaps. Words without a label keep no
  speaker rather than a guessed one.
- The smoke test uses the English samples shipped with NeMo-Speech.cpp. Accuracy
  on Vietnamese factory recordings (noise, jargon, overlapping speech) has not
  been evaluated here.
- Transcripts are not ingested automatically; ingest them explicitly and keep
  the JSON sidecar as provenance.
