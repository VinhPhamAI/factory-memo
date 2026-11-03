#!/usr/bin/env bash
# Checks the downloaded weights, then starts ai-services-api with the speech
# worker (ai-speech) and waits until speech is ready behind it.
# Usage: bash deployments/speech/up.sh [--no-build]
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

for file in "$ASR_DIR/viegem_asr.pt" "$ASR_DIR/asr/model.py" "$ENCODER_DIR/model.safetensors"; do
    [ -s "$file" ] || die "missing $file; run deployments/speech/setup.sh"
done

exec bash "$REPO_ROOT/deployments/ai-services-api/up.sh" "$@" speech
