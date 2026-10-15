#!/usr/bin/env bash
# End-to-end check of a running speech-api: health, models, HTTP transcription
# with diarization and realtime WebSocket streaming, using the sample WAVs from
# the NeMo-Speech.cpp checkout. Usage: bash deployments/speech/smoke_test.sh [audio.wav language]
# An optional WAV (e.g. a Vietnamese meeting) is also streamed in real time.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$REPO_ROOT"

JFK="$SOURCE_DIR/test_files/asr/wav/test/jfk.wav"
AMI="$SOURCE_DIR/test_files/diar/ami_en2002d_2132.wav"
OUT="${SPEECH_SMOKE_OUT:-$(mktemp -d)}"
mkdir -p "$OUT"
[ -f "$JFK" ] && [ -f "$AMI" ] || die "sample audio missing; run deployments/speech/setup.sh"
command -v jq >/dev/null && JQ=jq || JQ="python3 -m json.tool --no-ensure-ascii"

step() { printf '\n== %s\n' "$*"; }

# Streams a WAV over the realtime WebSocket with SpeechClient, prints each final
# as speaker turns and writes all turns to a file.
# Usage: stream_wav WAV LANGUAGE REALTIME(0|1) OUT_TXT
stream_wav() {
    uv run --no-sync python - "$@" "$SPEECH_API_URL" <<'PY'
import asyncio
import sys

from backend.src.services.speech_processing import (
    SpeechClient, SpeechSettings, apace, format_transcript, iter_pcm_chunks, read_wav_pcm16,
)

wav, language, realtime, out, url = sys.argv[1:6]


async def main() -> list:
    client = SpeechClient(SpeechSettings(base_url=url))
    pcm, rate = read_wav_pcm16(wav)
    chunks = iter_pcm_chunks(pcm, rate)
    audio = apace(chunks, rate) if realtime == "1" else chunks
    turns = []
    async for event in client.astream(audio, sample_rate=rate, language=language):
        if event.kind == "completed":
            turns.extend(event.utterances)
            print(format_transcript(event.utterances), flush=True)
    return turns


turns = asyncio.run(main())
with open(out, "w", encoding="utf-8") as handle:
    handle.write(format_transcript(turns) + "\n")
print(f"{len(turns)} turn(s) written to {out}", file=sys.stderr)
PY
}

step "health / ready / models ($SPEECH_API_URL)"
curl -fsS "$SPEECH_API_URL/health" | $JQ
curl -fsS "$SPEECH_API_URL/ready" | $JQ
curl -fsS "$SPEECH_API_URL/v1/models" | $JQ

step "HTTP transcription (jfk.wav, en-US)"
curl -fsS -X POST "$SPEECH_API_URL/v1/audio/transcriptions" \
    -F "file=@$JFK" -F language=en-US -F response_format=json -o "$OUT/jfk.json"
$JQ < "$OUT/jfk.json"
grep -qi "ask not" "$OUT/jfk.json" || die "jfk transcript does not contain 'ask not'"

step "HTTP transcription + diarization (AMI meeting sample)"
curl -fsS -X POST "$SPEECH_API_URL/v1/audio/transcriptions" \
    -F "file=@$AMI" -F language=en-US -F response_format=verbose_json \
    -F diarization=true -o "$OUT/ami.json"
speakers=$(uv run --no-sync python - "$OUT/ami.json" <<'PY'
import json, sys
words = json.load(open(sys.argv[1]))["words"]
print(len({w["speaker"] for w in words if w.get("speaker")}))
PY
)
log "AMI speakers found: $speakers"
[ "$speakers" -ge 2 ] || die "expected at least 2 speakers in the AMI sample"

step "realtime WebSocket stream + diarization (AMI meeting sample)"
stream_wav "$AMI" en-US 0 "$OUT/ami_stream.txt"
grep -q '^Speaker [0-9]' "$OUT/ami_stream.txt" || die "stream produced no speaker turns"

if [ -n "${1:-}" ]; then
    step "realtime stream of $1 (${2:-vi-VN}, paced like a live microphone)"
    stream_wav "$1" "${2:-vi-VN}" 1 "$OUT/custom_stream.txt"
fi

step "GPU"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv || true

log "all checks passed; outputs in $OUT"
