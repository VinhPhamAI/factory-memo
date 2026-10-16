# Voice capture

Live microphone recording in the React UI (`/capture`, tab **Ghi âm**), turned
into stored evidence, extracted knowledge drafts and a side-by-side review. The
same page also accepts a typed or pasted conversation (**Nhập văn bản**; see
[Typed text](#typed-text)), which goes through the same extraction and review
without audio.

```text
Browser mic ─ AudioWorklet (PCM16, 16 kHz) ─ WS /v1/voice/{kb}/record ─ FastAPI
                                                                          │
              data/audio/<time>_<name>.wav  ◄── every frame, written first ┤
              speech-api realtime ASR + diarization ◄── same frames ───────┘
Stop ─► transcript "[T01] Speaker A: …" ─► kb_<name>.raw_conversations (pending)
     ─► background: extraction → quote check → consolidation → knowledge_items (draft)
     ─► background (optional): LLM-cleaned copy of each turn, for reading only
Review page: transcript (raw / cleaned, audio per turn) | records (approve / reject)
```

Only knowledge bases with `retrieval="knowledge"` (`general`, `industrial_qa`
and knowledge bases created from the KB list on this page) accept recordings;
document-retrieval KBs do not search extracted records. Creating a knowledge base
is described in the [KB guide](../backend/knowledge_bases/README.md#knowledge-bases-created-in-the-ui).

## What is stored where

| Data | Location | Notes |
| --- | --- | --- |
| Original audio | `data/audio/<YYYYMMDD-HHMMSS>_<slug of the conversation name>.wav` (`VOICE_AUDIO_DIR`) | PCM16 mono at the browser's rate (normally 16 kHz). Written frame by frame, so it is complete even when recognition fails. |
| Recognizer output | Same name, `.asr.json` | Word timings, diarizer labels and confidences, and which recognition path was used. |
| Transcript (evidence) | `raw_conversations.transcript` in `kb_<name>` | Raw recognizer text, one `[Tnn] Speaker X: text` line per turn. Never rewritten. |
| Recording metadata | `raw_conversations.metadata` (JSONB) | See below. |
| Extracted knowledge | `knowledge_items` in `kb_<name>` | `evidence_id` points to the conversation; always created as `draft`. |
| Interrupted session | `…interrupted.wav` | Socket closed without Stop; kept on disk, not linked to any conversation. |

### Transcript format

```text
[T01] Speaker A: Trục Z kêu rè từ sáng.
[T02] Speaker B: Đã kiểm tra độ rơ chưa?
[T03] Unknown speaker: …
```

Letters follow the diarizer's labels within one recording (1 → A, 2 → B, …).
They name voices, not people; untagged speech is `Unknown speaker`, never a
guessed letter. Consecutive finals of one speaker join into one turn until a
pause of 2 s or more, or until the turn would span over 30 s, so a monologue
still has turns near the words they quote. The `[Tnn]` ids let
evidence quotes cite a turn and let quote verification check it.

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
  "asr": {"service": "nemo-speech.cpp", "language": "vi-VN", "diarization": true,
          "endpointing_ms": 800, "recognition": "realtime | file", "realtime_error": "…"},
  "speakers": {"A": {"name": "Anh Minh"}, "B": {"name": null}},
  "turns": [{"id": "T01", "speaker": "A", "start": 0.42, "end": 5.1}],
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

Speaker names are display labels added by a reviewer; the transcript keeps the
letters. `ingestion` and `cleaning` are progress records; a job saved as
`running` with no live task (API restarted) is reported as `interrupted` and
can be retried from the review page.

## Recognition and loss handling

- Every frame is written to the WAV file before it is sent to the speech service.
- One long realtime session can stop recognizing speech after a pause, so a new
  session starts in the first 0.6 s of quiet after each final (primed with that
  quiet audio). Realtime finals become the transcript.
- On stop the whole WAV is diarized once; speech the diarizer heard but no word
  covers is transcribed on its own and inserted (`asr.recovered_utterances`),
  and speaker letters come from that pass, so they stay consistent. During
  recording the live letters come from diarizing the audio so far.
- If realtime recognition fails or times out, recording continues and the
  finished WAV is transcribed piece by piece, cut at pauses
  (`asr.recognition = "file"`).
- If the browser loses the socket, it keeps recording locally; on Stop it uploads
  the whole WAV (`POST /v1/voice/{kb}/recordings`). A failed save offers a
  resend and a WAV download.
- A recording with no recognized speech is not stored as evidence; its audio file
  is kept and named in the error.
- The speech service must be ready before recording starts.

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
Content is never edited. There is no authentication: the actor is recorded as
the unauthenticated review UI.

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

WebSocket events from the server: `ready`, `partial` (text appended to the
utterance in progress), `final` (finished turns with speaker letters),
`recognition_lost`, `saving`, `saved` (`conversation_id`), `error` (`code`,
`message`, `audio_file`).

Settings: `VOICE_AUDIO_DIR`, `VOICE_VERIFY` (`off|quotes|llm`, default `quotes`),
`VOICE_CONSOLIDATE` (default `true`), `CLEANING_*`, plus the `SPEECH_*` settings.

## Limitations

- Accuracy of recognition and diarization on Vietnamese factory audio has not
  been evaluated; diarization can split one person into two letters or flip a
  short word to another letter.
- The microphone needs HTTPS or localhost.
- Job progress is in memory; restarting the API interrupts running jobs (they can
  be retried). There is no user authentication or per-user review identity.
- Verification checks quotes against the transcript, not engineering correctness.
