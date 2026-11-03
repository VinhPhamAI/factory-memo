#!/usr/bin/env bash
# End-to-end check of a running VieGem-ASR speech-api.
# Usage: bash deployments/speech/smoke_test.sh [speech.wav ["reference transcript"]]
#
# Without arguments: health, readiness, model list, the realtime WebSocket
# protocol on generated silence and refusal of diarization requests.
# With a WAV of Vietnamese speech (PCM16): HTTP transcription with word times,
# a realtime stream paced like a live microphone and the backend client's live
# tests. With a reference transcript, the word error rate is printed too.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$REPO_ROOT"

OUT="${SPEECH_SMOKE_OUT:-$(mktemp -d)}"
mkdir -p "$OUT"
command -v jq >/dev/null && JQ=jq || JQ="python3 -m json.tool --no-ensure-ascii"

step() { printf '\n== %s\n' "$*"; }

# Streams a WAV over the realtime WebSocket with the backend SpeechClient and
# writes the transcript. Usage: stream_wav WAV REALTIME(0|1) OUT_TXT
stream_wav() {
    uv run --no-sync python - "$@" "$SPEECH_API_URL" <<'PY'
import asyncio
import sys

from backend.src.services.speech_processing import (
    SpeechClient, SpeechSettings, apace, format_transcript, iter_pcm_chunks, read_wav_pcm16,
)

wav, realtime, out, url = sys.argv[1:5]


async def main() -> list:
    client = SpeechClient(SpeechSettings(base_url=url))
    pcm, rate = read_wav_pcm16(wav)
    chunks = iter_pcm_chunks(pcm, rate)
    audio = apace(chunks, rate) if realtime == "1" else chunks
    turns = []
    async for event in client.astream(audio, sample_rate=rate, language="vi-VN"):
        if event.kind == "completed":
            turns.extend(event.utterances)
            print(format_transcript(event.utterances), flush=True)
    return turns


turns = asyncio.run(main())
assert all(turn.speaker is None for turn in turns), "the service must not label speakers"
with open(out, "w", encoding="utf-8") as handle:
    handle.write(format_transcript(turns) + "\n")
print(f"{len(turns)} utterance(s) written to {out}", file=sys.stderr)
PY
}

step "health / ready / models ($SPEECH_API_URL)"
curl -fsS "$SPEECH_API_URL/health" | $JQ
curl -fsS "$SPEECH_API_URL/ready" | $JQ
curl -fsS "$SPEECH_API_URL/v1/models" | $JQ
curl -fsS "$SPEECH_API_URL/v1/models" | grep -q "5CD-AI/VieGem-ASR" \
    || die "speech-api does not serve VieGem-ASR"

step "realtime protocol on 3 s of silence; diarization refused"
uv run --no-sync python - "$SPEECH_API_URL" "$OUT/silence.wav" <<'PY'
import asyncio
import sys
import wave

import httpx

from backend.src.services.speech_processing import (
    SpeechClient, SpeechServiceError, SpeechSettings, iter_pcm_chunks,
)

url, path = sys.argv[1:3]
pcm = b"\x00\x00" * 16000 * 3
with wave.open(path, "wb") as writer:
    writer.setnchannels(1)
    writer.setsampwidth(2)
    writer.setframerate(16000)
    writer.writeframes(pcm)


async def main() -> None:
    client = SpeechClient(SpeechSettings(base_url=url))
    events = [e async for e in client.astream(iter_pcm_chunks(pcm, 16000), sample_rate=16000)]
    assert not [e for e in events if e.kind == "completed"], events
    with open(path, "rb") as handle:
        refused = httpx.post(f"{url}/v1/audio/transcriptions", data={"diarization": "true"},
                             files={"file": ("silence.wav", handle, "audio/wav")})
    assert refused.status_code == 400, refused.text
    print("silence: no finals; diarization request: HTTP 400")


asyncio.run(main())
PY

if [ -n "${1:-}" ]; then
    WAV="$1"
    step "HTTP transcription with word times ($WAV)"
    curl -fsS -X POST "$SPEECH_API_URL/v1/audio/transcriptions" \
        -F "file=@$WAV" -F language=vi-VN -F response_format=verbose_json -o "$OUT/file.json"
    uv run --no-sync python - "$OUT/file.json" "${2:-}" "$ASR_DIR" <<'PY'
import json
import sys

body, reference, model_dir = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
words = body["words"]
print(f"{body['duration']:.1f} s, {len(words)} words: {body['text'][:300]}")
assert words, "no words recognized"
assert all(w["start"] <= w["end"] and "speaker" not in w for w in words), words[:3]
if reference:
    sys.path.insert(0, model_dir)
    from asr.text import canon_spelling, normalize

    ref = canon_spelling(normalize(reference)).split()
    hyp = canon_spelling(normalize(body["text"])).split()
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    print(f"WER {100 * row[-1] / len(ref):.1f}% over {len(ref)} reference words")
PY

    step "realtime stream, paced like a live microphone ($WAV)"
    stream_wav "$WAV" 1 "$OUT/stream.txt"
    grep -q '^\[T01\] Unknown speaker: ' "$OUT/stream.txt" || die "stream produced no turns"

    step "backend client live tests"
    SPEECH_API_URL_TEST="$SPEECH_API_URL" SPEECH_TEST_WAV="$WAV" \
        uv run --no-sync python -m unittest backend.tests.test_speech_processing.LiveSpeechServiceTest
fi

step "GPU"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv || true

log "all checks passed; outputs in $OUT"
