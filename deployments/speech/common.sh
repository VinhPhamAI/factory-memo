# Shared helpers for the speech service scripts. Source from a script in this directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deployments/docker-compose.yml"
SOURCE_DIR="$REPO_ROOT/deployments/NeMo-Speech.cpp"
MODEL_DIR="$REPO_ROOT/models/nemo-speech"
ENV_FILE="$REPO_ROOT/.env"

NEMO_SPEECH_REPO="https://github.com/NVIDIA/NeMo-Speech.cpp.git"
NEMO_SPEECH_COMMIT="083861c241061562b8ec29eb06d7f6cdc3af8775"
ASR_REPO="nvidia/nemotron-3.5-asr-streaming-0.6b"
ASR_FILE="nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
DIAR_REPO="nvidia/Nemotron-3-Diarization"
DIAR_FILE="Nemotron-3-Diarization.q8_0.gguf"

# Prints one variable from .env without sourcing the whole file.
env_value() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 0
    sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
        | tail -n 1 | sed -E 's/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

SPEECH_API_PORT="${SPEECH_API_PORT:-$(env_value SPEECH_API_PORT)}"
SPEECH_API_PORT="${SPEECH_API_PORT:-8080}"
SPEECH_API_URL="${SPEECH_API_URL:-http://127.0.0.1:${SPEECH_API_PORT}}"

compose() {
    if [ -f "$ENV_FILE" ]; then
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

log() { printf '[speech] %s\n' "$*" >&2; }
die() { printf '[speech] ERROR: %s\n' "$*" >&2; exit 1; }
