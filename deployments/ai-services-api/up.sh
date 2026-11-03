#!/usr/bin/env bash
# Builds and starts ai-services-api with the named AI service workers, then
# waits until each named service is ready behind it.
# Usage: bash deployments/ai-services-api/up.sh [--no-build] [service ...]
#   service: a name from services.yaml (default: all of them). The worker of
#   service <name> is the Compose service ai-<name> (underscores as dashes).
# Speech needs its weights first: bash deployments/speech/setup.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
log() { printf '[ai-services] %s\n' "$*" >&2; }
die() { printf '[ai-services] ERROR: %s\n' "$*" >&2; exit 1; }
compose() {
    local env_args=()
    [ -f "$ENV_FILE" ] && env_args=(--env-file "$ENV_FILE")
    docker compose "${env_args[@]}" -f "$REPO_ROOT/deployments/docker-compose.yml" \
        -f "$REPO_ROOT/deployments/ai.compose.yml" "$@"
}

build_flag="--build"
if [ "${1:-}" = "--no-build" ]; then build_flag=""; shift; fi
names=("$@")
if [ ${#names[@]} -eq 0 ]; then
    mapfile -t names < <(sed -n -E 's/^  ([a-z][a-z0-9_]*):[[:space:]]*$/\1/p' "$HERE/services.yaml")
fi
workers=()
for name in "${names[@]}"; do
    grep -qE "^  ${name}:" "$HERE/services.yaml" || die "unknown service '$name' (see services.yaml)"
    workers+=("ai-${name//_/-}")
done

port="${AI_SERVICES_API_PORT:-$( [ -f "$ENV_FILE" ] && sed -n -E 's/^[[:space:]]*AI_SERVICES_API_PORT[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1)}"
url="${AI_SERVICES_API_URL:-http://127.0.0.1:${port:-8080}}"

log "starting ai-services-api with ${workers[*]} (first builds and model loads can take minutes)"
compose up -d $build_flag ai-services-api "${workers[@]}"

timeout_s="${AI_READY_TIMEOUT:-600}"
deadline=$((SECONDS + timeout_s))
for name in "${names[@]}"; do
    log "waiting up to ${timeout_s}s for $name ($url/v1/services/$name/ready)"
    until curl -fsS "$url/v1/services/$name/ready" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            compose logs --tail 60 "ai-${name//_/-}" >&2 || true
            die "$name did not become ready"
        fi
        sleep 5
    done
done
curl -fsS "$url/v1/services"; echo
log "ready at $url"
