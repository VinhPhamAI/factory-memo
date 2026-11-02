"""Check deployment boundaries before building the image."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_image_installs_independent_package_and_runs_nonroot():
    """Require a frozen installation and a nonroot serving command."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER agent" in dockerfile
    assert '"denso-agent", "serve", "--host", "0.0.0.0"' in dockerfile
    assert "backend" not in dockerfile


def test_runtime_compose_has_no_admin_credentials():
    """Keep setup credentials outside the runtime service mapping."""
    overlay = (ROOT.parent / "deployments" / "agent.compose.yml").read_text(
        encoding="utf-8"
    )
    runtime = overlay.split("  agent:\n", 1)[1]
    assert "BOOTSTRAP" not in runtime
    assert "SETUP_DATABASE" not in runtime
    assert "env_file:" not in runtime
    assert "AGENT_DATABASE_URL: ${AGENT_CONTAINER_DATABASE_URL" in runtime
    assert "condition: service_completed_successfully" in runtime
