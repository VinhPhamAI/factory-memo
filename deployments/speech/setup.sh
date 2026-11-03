#!/usr/bin/env bash
# Downloads the pinned VieGem-ASR checkpoint and EmbeddingGemma 2 encoder to
# models/ and checks the GPU/Docker prerequisites.
# Safe to re-run: files already downloaded are reused.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

log "checking prerequisites"
command -v uv >/dev/null || die "uv is required"
command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null || die "docker compose plugin is required"
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader \
    || die "nvidia-smi failed; an NVIDIA driver is required"
docker info 2>/dev/null | grep -qi nvidia \
    || die "NVIDIA Container Toolkit runtime is not registered with Docker"

HF_TOKEN="${HF_TOKEN:-$(env_value HF_TOKEN)}"
export HF_TOKEN
[ -n "$HF_TOKEN" ] || unset HF_TOKEN

download() {
    local repo="$1" revision="$2" dir="$3"
    shift 3
    log "downloading $repo@${revision:0:12} to $dir"
    (cd "$REPO_ROOT" && uv run --no-sync hf download "$repo" "$@" \
        --revision "$revision" --local-dir "$dir" >/dev/null)
}
download "$ASR_REPO" "$ASR_REVISION" "$ASR_DIR" "${ASR_FILES[@]}"
download "$ENCODER_REPO" "$ENCODER_REVISION" "$ENCODER_DIR" "${ENCODER_FILES[@]}"
# The server runs as uid 1000; the read-only mounts must be world-readable.
chmod -R a+rX "$ASR_DIR" "$ENCODER_DIR"
ls -l "$ASR_DIR/viegem_asr.pt" "$ENCODER_DIR/model.safetensors"

log "setup complete; next: bash deployments/speech/up.sh"
