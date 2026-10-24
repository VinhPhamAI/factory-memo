#!/usr/bin/env bash
# Start the Docker backend in order; only snapshot restore is best-effort.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[setup] %s\n' "$*" >&2; }
usage() {
    cat <<'EOF'
Usage: bash deployments/setup.sh [--skip-seed] [--no-build] [-- SNAPSHOT_PULL_ARGS...]

Build the backend image, stop existing API/workers, wait for PostgreSQL,
initialize shared DB tables, restore the Industrial QA snapshot, then start
API and workers in that order.
Only a seed failure is non-fatal. Existing data/volumes are retained.

  --skip-seed  Start services without downloading/rebuilding the snapshot.
  --no-build   Reuse the existing local/knowledge-agent-api image.
  --help      Show this help.

Snapshot defaults: HF_DATASET_REPO_ID from .env,
  --run-dir data/industrial_qa_runs/restored-test
  --run-id industrial-qa-test-full-20261010T164959Z
Arguments after -- override/extend these snapshot pull options.
EOF
}

skip_seed=false
build=true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-seed) skip_seed=true ;;
        --no-build) build=false ;;
        --help|-h) usage; exit 0 ;;
        --) shift; break ;;
        *) log "ERROR: unknown setup option: $1"; usage >&2; exit 2 ;;
    esac
    shift
done

phase=preflight
trap 'status=$?; log "ERROR: $phase failed (exit $status). Setup did not complete; inspect Docker logs and rerun."; exit "$status"' ERR

command -v docker >/dev/null || { log 'ERROR: Docker is required.'; exit 1; }
docker compose version >/dev/null
docker info >/dev/null

if [ ! -f .env ]; then
    cp .env.example .env
    log 'Created .env from .env.example. Configure model/embedding endpoints for seed and QA.'
fi

compose() {
    docker compose --env-file "$REPO_ROOT/.env" -f "$REPO_ROOT/deployments/docker-compose.yml" "$@"
}

# Validate without printing the resolved environment (which contains secrets).
compose config --quiet
mkdir -p data/setup

if [ "$build" = true ]; then
    phase='backend image build'
    log 'Building the image shared by seed, API and worker.'
    compose build api
fi

phase='stopping API and worker'
log 'Stopping existing API and worker before restoring data (volumes are retained).'
compose stop api worker

phase='PostgreSQL startup'
log 'Starting PostgreSQL and waiting for its healthcheck.'
compose up -d --wait --wait-timeout 120 postgres

phase='shared database initialization'
log 'Checking the container database connection and initializing shared tables.'
compose run --rm --no-deps -T api python -m backend.scripts.init_database

seed_status=skipped
seed_log=''
if [ "$skip_seed" = false ]; then
    phase='seed restore'
    seed_log="$(mktemp "$REPO_ROOT/data/setup/seed.XXXXXX")"
    log "Restoring seed in a temporary API-image container; log: $seed_log"
    # The runtime image deliberately excludes benchmark data. Supply only public
    # inputs; the API service supplies the same DATABASE_URL, env and data mount
    # used later by API/worker. No host Python/uv or exposed API port is needed.
    if compose run --rm --no-deps -T \
        --volume "$REPO_ROOT/benchmark/public:/app/benchmark/public:ro" \
        api python -m backend.knowledge_bases.industrial_qa.snapshot pull \
        --run-dir data/industrial_qa_runs/restored-test \
        --run-id industrial-qa-test-full-20261010T164959Z \
        "$@" 2>&1 | tee "$seed_log"; then
        seed_status=restored
    else
        seed_pipeline_status=("${PIPESTATUS[@]}")
        seed_exit=${seed_pipeline_status[0]}
        if [ "${seed_pipeline_status[1]}" -ne 0 ]; then
            log "ERROR: Could not write seed log: $seed_log"
            exit 1
        fi
        # Interrupting setup is an explicit stop, not a recoverable seed error.
        if [ "$seed_exit" -eq 130 ] || [ "$seed_exit" -eq 143 ]; then
            exit "$seed_exit"
        fi
        seed_status="failed (exit $seed_exit)"
        log "WARNING: Seed restore $seed_status; continuing with API and worker. See $seed_log"
    fi
fi

phase='API startup'
log 'Starting API and waiting for /healthz.'
compose up -d --no-deps --no-build --wait --wait-timeout 120 api

phase='worker startup'
log 'Starting worker after API is healthy.'
compose up -d --no-deps --no-build --wait --wait-timeout 120 worker

phase='service status'
compose ps postgres api worker
log "Setup complete. Seed: $seed_status."
if [[ "$seed_status" == failed* ]]; then
    log "WARNING: Services started with seed restore errors; data may be incomplete. See $seed_log"
fi
