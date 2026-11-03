#!/usr/bin/env bash
# Create the application database if it is missing, then apply Alembic revisions.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[migrate] %s\n' "$*" >&2; }
usage() {
    cat <<'EOF'
Usage: bash deployments/migrate.sh [--skip-postgres] [--skip-check]

Start the Compose PostgreSQL service, create the application database named in
MIGRATION_DATABASE_URL or APP_DATABASE_URL when it does not exist, run
`alembic upgrade head`, then `alembic check`. Safe to rerun.
Settings come from the environment, then .env. EMBEDDING_DIM is required.
The DATABASE_URL database is refused: it holds the service's own job table.

  --skip-postgres  Do not start Compose PostgreSQL (for another server).
  --skip-check     Do not compare models with the migrated schema.
  --help           Show this help.
EOF
}

start_postgres=true
run_check=true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-postgres) start_postgres=false ;;
        --skip-check) run_check=false ;;
        --help|-h) usage; exit 0 ;;
        *) log "ERROR: unknown option: $1"; usage >&2; exit 2 ;;
    esac
    shift
done

phase=preflight
trap 'status=$?; log "ERROR: $phase failed (exit $status)."; exit "$status"' ERR

command -v uv >/dev/null || { log 'ERROR: uv is required.'; exit 1; }
if [ ! -f .env ]; then
    log 'WARNING: .env not found; using the current environment only.'
fi

if [ "$start_postgres" = true ]; then
    phase='PostgreSQL startup'
    command -v docker >/dev/null || { log 'ERROR: Docker is required (or pass --skip-postgres).'; exit 1; }
    log 'Starting Compose PostgreSQL and waiting for its healthcheck.'
    env_file=()
    [ -f .env ] && env_file=(--env-file "$REPO_ROOT/.env")
    docker compose ${env_file[@]+"${env_file[@]}"} -f deployments/docker-compose.yml \
        up -d --wait --wait-timeout 120 postgres
fi

phase='database creation'
uv run python - <<'PY'
"""Create the migration target database on its server when it is missing."""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

# The script runs from the repository root; stdin scripts cannot search for .env.
load_dotenv(".env")
raw = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("APP_DATABASE_URL")
if not raw:
    sys.exit(
        "[migrate] ERROR: set APP_DATABASE_URL (or MIGRATION_DATABASE_URL) in .env; "
        "see .env.example"
    )
target = make_url(raw)
name = target.database
if not name:
    sys.exit("[migrate] ERROR: the database URL has no database name")

service = os.getenv("DATABASE_URL")
if service:
    service_url = make_url(service)
    same_server = (service_url.host, service_url.port) == (target.host, target.port)
    if same_server and service_url.database == name:
        sys.exit(
            f"[migrate] ERROR: database {name!r} is the DATABASE_URL database; "
            "use a separate application database"
        )

# CREATE DATABASE needs a connection to another database on the same server.
for maintenance in ("postgres", "template1"):
    engine = create_engine(target.set(database=maintenance), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"),
                {"name": name},
            )
            if exists:
                print(f"[migrate] Database {name!r} exists.", file=sys.stderr)
            else:
                quoted = conn.dialect.identifier_preparer.quote(name)
                conn.execute(text(f"CREATE DATABASE {quoted}"))
                print(f"[migrate] Created database {name!r}.", file=sys.stderr)
        break
    except OperationalError as exc:
        last_error = exc
    finally:
        engine.dispose()
else:
    sys.exit(
        f"[migrate] ERROR: cannot reach {target.host}:{target.port} as "
        f"{target.username!r}: {last_error.orig}"
    )
PY

phase='alembic upgrade'
log 'Applying revisions: alembic upgrade head.'
uv run alembic upgrade head

if [ "$run_check" = true ]; then
    phase='alembic check'
    log 'Comparing models with the migrated schema: alembic check.'
    uv run alembic check
fi

phase='revision status'
uv run alembic current
log 'Migration complete.'
