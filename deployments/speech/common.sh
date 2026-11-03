# Shared helpers for the speech service scripts. Source from a script in this directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# The speech worker (ai-speech) lives in the AI overlay behind ai-services-api;
# the base file keeps both in the backend project.
COMPOSE_FILE="$REPO_ROOT/deployments/docker-compose.yml"
AI_COMPOSE_FILE="$REPO_ROOT/deployments/ai.compose.yml"
MODEL_ROOT="$REPO_ROOT/models"
ENV_FILE="$REPO_ROOT/.env"

# Pinned Hugging Face revisions. VieGem-ASR holds the fine-tuned checkpoint and
# its model code (asr/); EmbeddingGemma 2 supplies the frozen encoder layers,
# tokenizer and feature-extractor settings.
ASR_REPO="5CD-AI/VieGem-ASR"
ASR_REVISION="95eae70b21fb0ef640e3ac4ea8cfa2380872ed20"
ASR_DIR="$MODEL_ROOT/VieGem-ASR"
ASR_FILES=(viegem_asr.pt asr/__init__.py asr/model.py asr/features.py asr/text.py
    asr/lm_decode.py README.md)
ENCODER_REPO="google/embeddinggemma-2"
ENCODER_REVISION="f9972ac22e696a105c482202646979e33e4b5a1e"
ENCODER_DIR="$MODEL_ROOT/embeddinggemma-2"
ENCODER_FILES=(config.json model.safetensors tokenizer.json tokenizer.model
    tokenizer_config.json preprocessor_config.json processor_config.json chat_template.jinja)

# Prints one variable from .env without sourcing the whole file.
env_value() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 0
    sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
        | tail -n 1 | sed -E 's/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# Clients reach the speech service through ai-services-api.
AI_SERVICES_API_PORT="${AI_SERVICES_API_PORT:-$(env_value AI_SERVICES_API_PORT)}"
AI_SERVICES_API_PORT="${AI_SERVICES_API_PORT:-8080}"
SPEECH_API_URL="${SPEECH_API_URL:-http://127.0.0.1:${AI_SERVICES_API_PORT}}"

compose() {
    if [ -f "$ENV_FILE" ]; then
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$AI_COMPOSE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" -f "$AI_COMPOSE_FILE" "$@"
    fi
}

log() { printf '[speech] %s\n' "$*" >&2; }
die() { printf '[speech] ERROR: %s\n' "$*" >&2; exit 1; }
