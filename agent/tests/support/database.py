"""Start PostgreSQL only when an infrastructure test requests it."""

import subprocess
import time
import uuid

import psycopg
import pytest


def docker(*arguments):
    """Run a Docker command and fail with its actual diagnostic output.

    Args:
        arguments: Docker command arguments without shell interpolation.

    Returns:
        Standard output from a successful command.
    """
    result = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode:
        pytest.fail(f"Docker infrastructure failed: {result.stderr.strip()}")
    return result.stdout.strip()


@pytest.fixture(scope="session")
def postgres_server():
    """Yield a unique PostgreSQL 17 container and remove only that container."""
    docker("version")
    name = f"denso-agent-test-{uuid.uuid4().hex[:12]}"
    docker(
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        "127.0.0.1::5432",
        "--env",
        "POSTGRES_PASSWORD=test-bootstrap",
        "postgres:17-alpine",
    )
    try:
        port = int(
            docker(
                "inspect",
                "--format",
                '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
                name,
            )
        )
        bootstrap = f"postgresql://postgres:test-bootstrap@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(bootstrap, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "Test PostgreSQL did not become ready within 60 seconds"
                    )
                time.sleep(0.1)
        yield {
            "host": "127.0.0.1",
            "port": port,
            "container": name,
            "bootstrap_url": bootstrap,
        }
    finally:
        docker("rm", "--force", "--volumes", name)


@pytest.fixture
def database(postgres_server):
    """Provide an empty agent database target inside the test-owned container."""
    bootstrap = postgres_server["bootstrap_url"]
    with psycopg.connect(bootstrap, autocommit=True) as connection:
        connection.execute("DROP DATABASE IF EXISTS agent_memory WITH (FORCE)")
        connection.execute("DROP ROLE IF EXISTS agent_runtime")
        connection.execute("DROP ROLE IF EXISTS agent_owner")
    address = f"127.0.0.1:{postgres_server['port']}/agent_memory"
    return {
        "bootstrap_database_url": bootstrap,
        "setup_database_url": f"postgresql://agent_owner:test-owner@{address}",
        "database_url": f"postgresql://agent_runtime:test-runtime@{address}",
    }
