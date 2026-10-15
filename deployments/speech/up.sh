#!/usr/bin/env bash
# Builds (if needed) and starts the speech-api service, then waits for /ready.
# Usage: bash deployments/speech/up.sh [--no-build]
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[ -f "$SOURCE_DIR/docker/Dockerfile" ] || die "missing $SOURCE_DIR; run deployments/speech/setup.sh"
for file in "$ASR_FILE" "$DIAR_FILE"; do
    [ -s "$MODEL_DIR/$file" ] || die "missing $MODEL_DIR/$file; run deployments/speech/setup.sh"
done

build_flag="--build"
[ "${1:-}" = "--no-build" ] && build_flag=""

log "starting speech-api (first build compiles CUDA kernels and can take a while)"
compose up -d $build_flag speech-api

timeout_s="${SPEECH_READY_TIMEOUT:-300}"
log "waiting up to ${timeout_s}s for $SPEECH_API_URL/ready"
deadline=$((SECONDS + timeout_s))
until curl -fsS "$SPEECH_API_URL/ready" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        compose logs --tail 80 speech-api >&2 || true
        die "speech-api did not become ready"
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' speech-api 2>/dev/null)" != "true" ]; then
        compose logs --tail 80 speech-api >&2 || true
        die "speech-api container is not running"
    fi
    sleep 3
done
curl -fsS "$SPEECH_API_URL/ready"; echo
log "speech-api is ready at $SPEECH_API_URL"
