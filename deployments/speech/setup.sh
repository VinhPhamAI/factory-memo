#!/usr/bin/env bash
# Fetches the pinned NeMo-Speech.cpp source and the ASR + diarization GGUF models,
# checks the GPU/Docker prerequisites and logs in to nvcr.io with NGC_API_KEY.
# Safe to re-run: existing checkouts and downloads are reused.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

log "checking prerequisites"
command -v git >/dev/null || die "git is required"
command -v uv >/dev/null || die "uv is required"
command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null || die "docker compose plugin is required"
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader \
    || die "nvidia-smi failed; an NVIDIA driver is required"
docker info 2>/dev/null | grep -qi nvidia \
    || die "NVIDIA Container Toolkit runtime is not registered with Docker"

log "NeMo-Speech.cpp source at $SOURCE_DIR (commit $NEMO_SPEECH_COMMIT)"
if [ ! -d "$SOURCE_DIR/.git" ]; then
    git clone --recurse-submodules "$NEMO_SPEECH_REPO" "$SOURCE_DIR"
fi
git -C "$SOURCE_DIR" fetch --quiet origin "$NEMO_SPEECH_COMMIT" 2>/dev/null || true
git -C "$SOURCE_DIR" -c advice.detachedHead=false checkout --quiet "$NEMO_SPEECH_COMMIT"
git -C "$SOURCE_DIR" submodule update --init --recursive
log "source at $(git -C "$SOURCE_DIR" rev-parse HEAD)"

log "downloading models to $MODEL_DIR"
mkdir -p "$MODEL_DIR"
HF_TOKEN="${HF_TOKEN:-$(env_value HF_TOKEN)}"
export HF_TOKEN
[ -n "$HF_TOKEN" ] || unset HF_TOKEN
(cd "$REPO_ROOT" && uv run --no-sync hf download "$ASR_REPO" "$ASR_FILE" --local-dir "$MODEL_DIR")
(cd "$REPO_ROOT" && uv run --no-sync hf download "$DIAR_REPO" "$DIAR_FILE" --local-dir "$MODEL_DIR")
# The runtime image runs as uid 1000; the read-only mount must be world-readable.
chmod -R a+rX "$MODEL_DIR"
ls -l "$MODEL_DIR"/*.gguf

NGC_API_KEY="${NGC_API_KEY:-$(env_value NGC_API_KEY)}"
if [ -n "$NGC_API_KEY" ]; then
    log "logging in to nvcr.io with NGC_API_KEY"
    printf '%s' "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin >/dev/null \
        || log "warning: nvcr.io login failed; the public CUDA base image may still pull anonymously"
else
    log "NGC_API_KEY not set; pulling the public CUDA base image anonymously"
fi

log "setup complete; next: bash deployments/speech/up.sh"
