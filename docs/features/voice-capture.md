# Voice capture

Live microphone recording in the React UI (`/capture`, tab **Ghi âm**), turned
into stored transcript evidence. New UI recordings wait for per-source extraction
mode selection and side-by-side review in **Quản lý tri thức** (`/knowledge`);
see the [management guide](knowledge-management.md), unless **Auto · chạy đến
Extract** is selected: the recording then sends `extract_mode` and, once saved,
an extraction job is queued for the [job workers](knowledge-management.md#job-queue-and-workers).
Legacy API clients without `extract_mode` still extract in the API process by
default unless they send `extract=false`. The
same page offers **Tải lên tài liệu** for storage-only document uploads, independent
of conversations; see [document uploads](document-uploads.md). The typed-text API
remains available to existing clients (see [Typed text](#typed-text)).

```text
Browser mic ─ AudioWorklet (PCM16, 16 kHz) ─ WS /v1/voice/{kb}/record ─ FastAPI
                                                                          │
              data/audio/<time>_<name>.wav  ◄── every frame, written first ┤
     ai-services-api → ai-speech realtime ASR (VieGem-ASR) ◄── same frames ┘
Stop ─► transcript "[T01] Unknown speaker: …" ─► kb_<name>.raw_conversations (pending)
     ─► Quản lý tri thức: select mode → extraction → knowledge_items/chunks (draft)
     ─► background (optional): LLM-cleaned copy of each turn, for reading only
Management detail: transcript (raw / cleaned, audio per turn) | results (approve / reject)
```

Only knowledge bases with `retrieval="knowledge"` (`general`, `industrial_qa`
and knowledge bases created from the KB list on this page) accept recordings;
document-retrieval KBs do not search extracted records. Creating a knowledge base
is described in the [KB guide](../../backend/knowledge_bases/README.md#knowledge-bases-created-in-the-ui).

## What is stored where

| Data | Location | Notes |
| --- | --- | --- |
| Original audio | `data/audio/<YYYYMMDD-HHMMSS>_<slug of the conversation name>.wav` (`VOICE_AUDIO_DIR`) | PCM16 mono at the browser's rate (normally 16 kHz). Written frame by frame, so it is complete even when recognition fails. |
| Recognizer output | Same name, `.asr.json` | Word timings and confidences, and which recognition path was used. |
| Transcript (evidence) | `raw_conversations.transcript` in `kb_<name>` | Raw recognizer text, one `[Tnn] Speaker X: text` line per turn. Never rewritten. |
| Recording metadata | `raw_conversations.metadata` (JSONB) | See below. |
| Extracted knowledge | `knowledge_items` in `kb_<name>` | `evidence_id` points to the conversation; always created as `draft`. |
| Interrupted session | `…interrupted.wav` | Socket closed without Stop; kept on disk, not linked to any conversation. |

### Transcript format

```text
[T01] Unknown speaker: trục z kêu rè từ sáng đã kiểm tra độ rơ chưa
[T02] Unknown speaker: chưa kiểm tra
```

The speech service ([VieGem-ASR](../../deployments/speech/README.md)) does not
label speakers, so every recorded turn is `Unknown speaker`; a guessed letter
is never added. Its text is lowercase without punctuation, with numbers
written as words. Consecutive finals join into one turn until a pause of 2 s or
more, or until the turn would span over 30 s, so turns stay near the words they
quote; one turn can hold several people's speech. The `[Tnn]` ids let evidence
quotes cite a turn and let quote verification check it. Typed conversations
keep speaker letters (see [Typed text](#typed-text)); recordings made before
the switch keep the letters their diarizer gave them.

### `raw_conversations.metadata`

```json
{
  "source_kind": "voice_recording",
  "kb": "general",
  "name": "Kiểm tra rung trục Z",
  "capture_mode": "live | upload",
  "recorded_at": "2026-10-15T07:32:05+00:00",
  "transcript_sha256": "…",
  "audio": {"file": "….wav", "asr_file": "….asr.json", "sha256": "…",
            "duration_s": 312.4, "sample_rate": 16000, "format": "wav/pcm16/mono"},
  "asr": {"service": "viegem-asr", "model": "5CD-AI/VieGem-ASR", "language": "vi-VN",
          "diarization": "none", "endpointing_ms": 800, "recognition": "realtime | file",
          "realtime_error": "…"},
  "speakers": {},
  "turns": [{"id": "T01", "speaker": null, "start": 0.42, "end": 5.1}],
  "extraction": {"version": "general-items-v2", "model": "…", "prompt_sha256": "…"},
  "ingestion": {"status": "queued | running | done | failed", "started_at": "…",
                "finished_at": "…", "error": "…", "stored": 3,
                "merged": [{"subject": "…", "claim": "…", "type": "heuristic", "target": "<record id>"}],
                "consolidation": {"add": 2, "merge": 1}},
  "cleaning": {"enabled": true, "status": "off | queued | running | done | failed",
               "model": "…", "total": 40,
               "turns": {"T01": {"text": "…"},
                         "T02": {"text": "<raw>", "kept_raw": "numbers_changed", "missing_numbers": ["0.05"]}}}
}
```

Speaker names are display labels a reviewer adds to lettered speakers (typed
conversations and older recordings); the transcript keeps the letters. `ingestion` and `cleaning` are progress records; a job saved as
`running` with no live task (API restarted) is reported as `interrupted` and
can be retried from the review page.

## Recognition and loss handling

- Every frame is written to the WAV file before it is sent to the speech service.
- One long realtime session can stop recognizing speech after a pause, so a new
  session starts in the first 0.6 s of quiet after each final (primed with that
  quiet audio). Realtime finals become the transcript.
- If realtime recognition fails or times out, recording continues and the
  finished WAV is transcribed piece by piece, cut at pauses into pieces of at
  most 30 s (`asr.recognition = "file"`).
- If the browser loses the socket, it keeps recording locally; on Stop it uploads
  the whole WAV (`POST /v1/voice/{kb}/recordings`). A failed save offers a
  resend and a WAV download.
- A recording with no recognized speech is not stored as evidence; its audio file
  is kept and named in the error.
- The speech service must be ready before recording starts.
- The speech service's voice activity detection (Silero VAD) only decides how
  often recognition runs; it never removes audio or words. Its
  `speech_started`/`speech_stopped` events are not used by the backend.

## Typed text

`POST /v1/voice/{kb}/texts` with `{"name", "text", "dialogue"}` stores the text
verbatim as a `[Tnn] Speaker X:` transcript (`source_kind: "typed_text"`,
`capture_mode: "text" | "dialogue"`, no `audio`/`asr`) and starts extraction.
Nothing is reworded or guessed:

- Off (default): one author, Speaker A; one turn per paragraph (blank-line
  separated), or per line when there are no blank lines.
- `dialogue`: every `Name: text` line starts a turn; names are lettered A, B, …
  in order of appearance and kept as display names (as a reviewer's rename
  would be); lines without a name continue the turn above. A first line
  without a name is refused. Detecting this automatically was rejected because
  notes such as `Bước 1: …` would silently become speakers.
- Text already in `[T01] Speaker A: …` form keeps its speakers (renumbered).

Typed text is never cleaned (`POST …/clean` returns 409). Limit: 100,000 characters.

## Transcript cleaning

Optional (toggle on the start screen, or **Làm sạch bằng AI** later). An LLM
(`CLEANING_*`, falling back to `OPENROUTER_*`) removes fillers, repetitions and
false starts per turn, in batches. It is a reading aid only: extraction and
evidence quotes use the raw transcript, and a cited turn is always shown raw with
the quote highlighted. A cleaned turn that loses a number keeps its raw text
(`kept_raw: numbers_changed`).

## Review

Records start as `draft` and stay searchable. **Duyệt** sets `approved`,
**Loại bỏ** sets `deprecated` (excluded from search), **Hoàn tác** returns to
`draft`; each decision is appended to `revision.history` with an optional note.
Content is never edited. Review requires an expert or administrator, and the
actor is recorded as that signed-in account.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/voice/knowledge-bases` | KBs that accept recordings |
| WS | `/v1/voice/{kb}/record` | Live recording (`start` → PCM16 frames → `stop`) |
| POST | `/v1/voice/{kb}/recordings` | Whole WAV upload (fallback) |
| POST | `/v1/voice/{kb}/texts` | Typed or pasted conversation |
| GET | `/v1/voice/{kb}/conversations` | Recent recordings |
| GET | `/v1/voice/{kb}/conversations/{id}` | Turns, speakers, job progress, records |
| GET | `/v1/voice/{kb}/conversations/{id}/audio` | Original WAV (supports Range) |
| PATCH | `/v1/voice/{kb}/conversations/{id}/speakers` | `{"speakers": {"A": "Anh Minh"}}` |
| POST | `/v1/voice/{kb}/conversations/{id}/extract` | Retry failed/interrupted extraction |
| POST | `/v1/voice/{kb}/conversations/{id}/clean` | Start (or retry) cleaning |
| POST | `/v1/voice/{kb}/knowledge/{record_id}/review` | `{"decision": "approve" \| "reject" \| "reset", "note": "…"}` |

Every HTTP route requires a bearer access token. The WebSocket `start` message
includes `access_token`; the server authenticates it before accepting audio.

WebSocket events from the server: `ready`, `partial` (text appended to the
utterance in progress), `turns` (finished turns; recorded turns have no speaker letter),
`recognition_lost`, `saving`, `saved` (`conversation_id`; with `extract_mode`
also `job` or `job_error`), `error` (`code`, `message`, `audio_file`). The WS
`start` message and the `/recordings` form accept `extract_mode`
(`knowledge|documents`); a queueing failure never loses the saved transcript.

Settings: `VOICE_AUDIO_DIR`, `VOICE_VERIFY` (`off|quotes|llm`, default `quotes`),
`VOICE_CONSOLIDATE` (default `true`), `CLEANING_*`, plus the `SPEECH_*` settings.
`industrial_qa` extracts, verifies and consolidates with the `[ingestion]` and
`[llm.*]` settings of its `config.toml` (`INDUSTRIAL_QA_CONFIG`), the same
contract as its benchmark runners; `VOICE_VERIFY`/`VOICE_CONSOLIDATE` apply only
where that file omits `verify`/`consolidate`.

## Limitations

- Recordings have no speaker labels; reviewers must tell speakers apart from
  the audio.
- Accuracy on Vietnamese factory audio has not been evaluated. On FLEURS
  Vietnamese (read speech) the deployed service measured 6.5% WER; see the
  [speech service guide](../../deployments/speech/README.md).
- The microphone needs HTTPS or localhost.
- Legacy in-process extraction and cleaning progress is in memory; restarting
  the API interrupts them (they can be retried). Queued jobs survive restarts. There is no user authentication or per-user review identity.
- Verification checks quotes against the transcript, not engineering correctness.
