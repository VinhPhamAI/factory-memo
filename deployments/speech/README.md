# Speech service (VieGem-ASR)

`speech-api` in the AI overlay [`deployments/ai.compose.yml`](../ai.compose.yml)
serves [5CD-AI/VieGem-ASR](https://huggingface.co/5CD-AI/VieGem-ASR) with a
FastAPI server in [`viegem_server/`](viegem_server/):

- **ASR:** VieGem-ASR, a 359M-parameter CTC model for Vietnamese and
  Vietnamese–English code-switching on the EmbeddingGemma 2 audio encoder.
  Output is lowercase without punctuation; numbers are written as words.
- **No speaker diarization.** Every utterance is unlabelled
  (`Unknown speaker`); requests asking for diarization get an error.
- **Endpointing:** an utterance ends once `SPEECH_EOU_MS` (default 800 ms)
  passes after the last recognized word. Noise without words never ends one.
- **Voice activity detection (realtime only):** [Silero VAD](https://github.com/snakers4/silero-vad)
  6.2.3 on the CPU decides how often the ASR model runs and reports
  `speech_started` / `speech_stopped`. It never removes audio or words.
- **Word times** come from the CTC alignment (40 ms frames). CTC marks a word
  near its start, so `end` is only one frame later than `start`.

The overlay joins the backend Compose project (`knowledge-agent`), so the API
container reaches the service at `http://speech-api:8080`. The port is bound to
`127.0.0.1` without an API key; put an authenticating TLS proxy in front before
exposing it beyond the host.

**License:** the model is CC-BY-NC-SA-4.0 (non-commercial research use) and
also subject to the Gemma Terms of Use. Check both before any other use.
Silero VAD is MIT-licensed.

## Run

From the repository root (`.env` may supply `HF_TOKEN` and the `SPEECH_*`
settings in [`.env.example`](../../.env.example)):

```sh
bash deployments/speech/setup.sh       # GPU/Docker checks, model downloads
bash deployments/speech/up.sh          # build image, start, wait for /ready
bash deployments/speech/smoke_test.sh  # health, protocol, diarization refused
bash deployments/speech/smoke_test.sh speech.wav "reference text"  # + accuracy
docker compose -f deployments/docker-compose.yml -f deployments/ai.compose.yml logs -f speech-api
```

`setup.sh` downloads pinned revisions into `models/` (ignored by Git), mounted
read-only into the container. The image downloads nothing at runtime.

| Folder | Repository | Contents |
| --- | --- | --- |
| `models/VieGem-ASR/` | `5CD-AI/VieGem-ASR` | `viegem_asr.pt` checkpoint (311 MB), model code `asr/`, model card |
| `models/embeddinggemma-2/` | `google/embeddinggemma-2` | Encoder weights (1.5 GB), tokenizer, feature-extractor settings |

The image is `python:3.12-slim` with PyTorch 2.14.1 (CUDA 12.6 wheels) and the
packages pinned in [`requirements.txt`](requirements.txt). The Silero VAD model
ships inside the pinned `silero-vad` package and runs with ONNX Runtime. The
service needs about 2 GB of GPU memory; `SPEECH_GPU_ID` selects the device.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SPEECH_API_PORT` | `8080` | Host port (bound to `127.0.0.1`) |
| `SPEECH_GPU_ID` | `0` | GPU used by the container |
| `SPEECH_EOU_MS` | `800` | Default end-of-utterance silence; sessions may override it (200–5000) |
| `SPEECH_LANGUAGE` | `vi-VN` | Language echoed in responses; it does not change recognition |
| `SPEECH_MAX_BATCH` | `8` | Clips per GPU batch |
| `SPEECH_COMPILE` | `0` | `1` enables `torch.compile`; benchmark before enabling |
| `SPEECH_VAD` | `1` | `0` turns voice activity detection off for all sessions |
| `SPEECH_VAD_THRESHOLD` | `0.5` | Speech probability (0.1–0.9) a 32 ms chunk needs to start speech; sessions may override it |

## API

- `GET /health` (process up), `GET /ready` (models loaded, 503 before; `vad`
  names the VAD or is `null`), `GET /v1/models`.
- `POST /v1/audio/transcriptions`: multipart `file` (WAV, FLAC, OGG, …, any
  rate, mono or stereo) and `response_format` `json`, `text` or `verbose_json`.
  `verbose_json` returns `duration`, `text` and `words[]` with `word`, `start`,
  `end`, `confidence`. Files over 30 s are cut at their quietest point between
  20 s and 30 s and transcribed as one batch. Limits: 200 MB, one hour.
- `POST /transcribe`: multipart `file`; returns `{"text": "..."}`.
- WebSocket `/v1/audio/transcriptions/realtime`: wait for `session.created`,
  send at most one `session.update` before any audio, then binary mono PCM16
  frames (8–96 kHz), and finish with `{"type": "input_audio_buffer.commit"}`:

  ```json
  {"type": "session.update", "session": {"sample_rate": 16000, "language": "vi-VN",
   "word_timestamps": true, "endpointing_ms": 800, "vad": true, "vad_threshold": 0.5}}
  ```

  The server sends `conversation.item.input_audio_transcription.delta`
  (text to append to the utterance in progress),
  `.completed` (`transcript` and `words`, times in seconds since the session's
  first frame), `input_audio_buffer.speech_started` (`audio_start_ms`) and
  `input_audio_buffer.speech_stopped` (`audio_end_ms`) when the VAD is on,
  `input_audio_buffer.committed` (last event) and `error`. `vad: true` is an
  error when the server runs with `SPEECH_VAD=0`.

### How realtime recognition works

The model is not run incrementally. The session re-transcribes the utterance in
progress after every 0.4 s of new audio (the model runs hundreds of times
faster than real time):

1. Once the last word was emitted at least `endpointing_ms` before the end of
   the buffer, a `completed` event is sent and the buffer is cut in the middle
   of that pause.
2. Partials only append: all words except the last are sent once they extend
   what was sent. After a revision no more partials are sent for that
   utterance; its `completed` event holds the corrected text.
3. An utterance reaching 25 s is cut at its largest pause between words.
4. Audio without words is dropped, keeping the last 0.5 s.

With the VAD on, every frame is also rated as it arrives (about 0.15 ms per
32 ms chunk on one CPU thread). The VAD only changes when the model runs:

- While it hears no speech in the buffer and the last run found no words, the
  model runs every 2 s instead of every 0.4 s. Speech the VAD misses is still
  recognized, up to 2 s later.
- Once it has heard `endpointing_ms` without speech after a region, the model
  runs at once, so the final does not wait for the next 0.4 s step.
- Its state is reset after 1 s without speech. Otherwise it adapts to a loud
  speaker and misses a much quieter one that follows: on 200 FLEURS clips
  joined with random gains of 0 to -20 dB, it covered 89.9% of the words
  without the reset and 97.2% with it. This is why it never drops words.

Whole-file transcription does not use the VAD: the model is fast enough on
silence, and on the synthetic noises tried (white, brown, 50 Hz hum, motor
whine, clanks; 60 s each) it produced no words with or without the VAD.

On near-silent audio the model tends to emit a word at the very last frame of
its input. Each clip is therefore padded with 0.4 s of silence, and words
starting in that padding are dropped.

## Python client

[`backend/src/services/speech_processing.py`](../../backend/src/services/speech_processing.py)
wraps both paths (`SpeechClient.atranscribe_file`, `atranscribe_segments`,
`astream`). `format_transcript` renders `[T01] Unknown speaker: text` lines,
which `backend.scripts.kb ingest --file` and the UI's voice capture
([docs/features/voice-capture.md](../../docs/features/voice-capture.md)) store;
`Utterance.to_json` keeps word timings as provenance.

```python
client = SpeechClient()  # SPEECH_API_URL, SPEECH_LANGUAGE, SPEECH_EOU_MS
utterances = await client.atranscribe_segments("meeting.wav", language="vi-VN")
pcm, rate = read_wav_pcm16("meeting.wav")
async for event in client.astream(apace(iter_pcm_chunks(pcm, rate), rate), sample_rate=rate):
    if event.kind == "completed":
        print(format_transcript(event.utterances))
```

## Tests

```sh
# Server logic with a stub engine (no GPU or weights):
uv run python -m unittest discover -s deployments/speech/tests -t deployments/speech -v
# Backend client against a running service and a WAV of Vietnamese speech:
SPEECH_API_URL_TEST=http://127.0.0.1:8080 SPEECH_TEST_WAV=speech.wav \
  uv run python -m unittest backend.tests.test_speech_processing -v
```

Measured on one A40 with the deployed service (greedy decoding, FLEURS
Vietnamese dev, read speech; references normalized with the model's
`asr.text.normalize`):

| Check | Result |
| --- | --- |
| HTTP, 100 clips (1,238 s) | WER 6.5%, about 540× real time |
| Realtime stream, 12 clips joined with 1.5 s pauses (162 s) | WER 6.2% |
| Same recording, whole-file path (`atranscribe_segments`) | WER 5.4–7.0% |
| 100 silent or near-silent clips (digital zero to about −60 dBFS) | No words |
| Realtime, 30 clips joined with 1.5 s noise gaps (388 s), VAD off / on | WER 7.07% / 6.50%; endpoint delay 0.98 / 0.91 s mean, 1.14 / 1.13 s p90 |
| Same with random clip gains of 0 to −20 dB, VAD off / on | WER 7.30% / 7.18%; delay 0.98 / 0.92 s mean |

## Limitations

- No speaker labels: reviewers must tell speakers apart from the audio.
- Accuracy on Vietnamese factory recordings (noise, jargon, overlapping
  speech) has not been evaluated. FLEURS is read speech; the model card reports
  9.5% WER on spontaneous YouTube speech.
- Text has no punctuation or capitals, and numbers are written as words
  ("không phẩy không năm"), which extraction and quote checks must handle.
- The optional KenLM language model (`lm/` in the model repository; 5.3% vs
  6.3% mean WER in the model card) is neither downloaded nor used; it needs
  `kenlm` and a beam search.
- Transcripts are not ingested automatically; ingest them explicitly and keep
  the JSON sidecar as provenance.
