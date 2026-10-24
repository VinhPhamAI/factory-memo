"""Offline checks for setup ordering and its seed-only failure boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend.scripts.init_database import initialize_database


ROOT = Path(__file__).resolve().parents[2]
FAKE_DOCKER = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["compose", "version"]:
    step = "version"
elif args == ["info"]:
    step = "info"
else:
    assert args[:2] == ["compose", "--env-file"], args
    assert Path(args[2]).resolve() == Path.cwd() / ".env", args
    assert args[3] == "-f", args
    assert Path(args[4]).resolve() == Path.cwd() / "deployments/docker-compose.yml", args
    command = args[5:]
    if command[0] == "run":
        step = "database" if "backend.scripts.init_database" in command else "seed"
    elif command[0] == "up":
        step = command[-1]
    else:
        step = command[0]

with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"step": step, "args": args}) + "\n")
if step in os.environ.get("FAKE_FAIL_STEP", "").split(","):
    print("simulated " + step + " error", file=sys.stderr)
    sys.exit(int(os.environ.get("FAKE_FAIL_CODE", "7")))
if step == "seed":
    print("simulated seed restored")
'''


class DockerSetupTests(unittest.TestCase):
    def run_setup(self, *args, fail=None, code=7, existing_env=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "checkout with spaces"
        (root / "deployments").mkdir(parents=True)
        shutil.copy2(ROOT / "deployments/setup.sh", root / "deployments/setup.sh")
        # These are placeholders; the fake Docker never contacts real services.
        (root / "deployments/docker-compose.yml").touch()
        (root / ".env.example").write_text("HF_DATASET_REPO_ID=org/example\n")
        if existing_env:
            (root / ".env").write_text("KEEP_EXISTING=config\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER)
        docker.chmod(0o755)
        call_log = root / "calls.jsonl"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(call_log),
            "FAKE_FAIL_STEP": fail or "",
            "FAKE_FAIL_CODE": str(code),
        }
        # Invoked outside the checkout to verify root-relative paths/quoting.
        result = subprocess.run(
            ["bash", str(root / "deployments/setup.sh"), *args],
            cwd=temporary.name, env=env, text=True, capture_output=True, timeout=20,
        )
        calls = [json.loads(line) for line in call_log.read_text().splitlines()]
        return result, calls, root

    def test_seed_finishes_before_api_then_worker_start(self):
        result, calls, root = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["step"] for c in calls], [
            "version", "info", "config", "build", "stop", "postgres",
            "database", "seed", "api", "worker", "ps",
        ])
        seed = next(c["args"] for c in calls if c["step"] == "seed")
        self.assertIn("--no-deps", seed)
        self.assertIn("--rm", seed)
        self.assertIn(f"{root}/benchmark/public:/app/benchmark/public:ro", seed)
        self.assertIn("backend.knowledge_bases.industrial_qa.snapshot", seed)
        for service in ("postgres", "api", "worker"):
            command = next(c["args"] for c in calls if c["step"] == service)
            self.assertIn("--wait", command)
        self.assertEqual((root / ".env").read_text(), "KEEP_EXISTING=config\n")
        self.assertIn("Seed: restored", result.stderr)

    def test_seed_error_is_logged_and_services_still_start_with_exit_zero(self):
        result, calls, root = self.run_setup(fail="seed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["step"] for c in calls][-3:], ["api", "worker", "ps"])
        self.assertIn("WARNING: Seed restore failed (exit 7)", result.stderr)
        self.assertIn("data may be incomplete", result.stderr)
        logs = list((root / "data/setup").glob("seed.*"))
        self.assertEqual(len(logs), 1)
        self.assertIn("simulated seed error", logs[0].read_text())

    def test_infrastructure_errors_stop_the_remaining_steps(self):
        for step in ("config", "build", "stop", "postgres", "database", "api", "worker"):
            with self.subTest(step=step):
                result, calls, _ = self.run_setup(fail=step)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(calls[-1]["step"], step)
                self.assertNotIn("Setup complete", result.stderr)

    def test_seed_error_does_not_hide_a_later_api_failure(self):
        result, calls, _ = self.run_setup(fail="seed,api")
        self.assertEqual(result.returncode, 7)
        self.assertIn("WARNING: Seed restore failed", result.stderr)
        self.assertNotIn("worker", [c["step"] for c in calls])

    def test_skip_seed_and_build_reuses_image_but_still_checks_database(self):
        result, calls, _ = self.run_setup("--skip-seed", "--no-build")
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = [c["step"] for c in calls]
        self.assertNotIn("seed", steps)
        self.assertNotIn("build", steps)
        self.assertIn("database", steps)
        self.assertEqual(steps[-3:], ["api", "worker", "ps"])

    def test_snapshot_options_are_passed_as_separate_arguments(self):
        options = ["--repo-id", "org/custom", "--revision", "v2", "--run-dir", "data/custom run"]
        result, calls, _ = self.run_setup("--", *options)
        self.assertEqual(result.returncode, 0, result.stderr)
        seed = next(c["args"] for c in calls if c["step"] == "seed")
        self.assertEqual(seed[-len(options):], options)

    def test_missing_env_is_created_from_example(self):
        result, _, root = self.run_setup("--skip-seed", existing_env=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / ".env").read_text(), (root / ".env.example").read_text())
        self.assertIn("Created .env", result.stderr)

    def test_seed_interrupt_does_not_start_services(self):
        result, calls, _ = self.run_setup(fail="seed", code=130)
        self.assertEqual(result.returncode, 130)
        self.assertEqual(calls[-1]["step"], "seed")


class DatabaseInitializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_initializes_registry_and_queue_without_claiming_jobs(self):
        registry, queue = AsyncMock(), AsyncMock()
        with (
            patch("backend.scripts.init_database.server_url_from_env", return_value="configured-url"),
            patch("backend.scripts.init_database.KnowledgeBaseRegistry.from_url", return_value=registry) as factory,
            patch("backend.scripts.init_database.JobQueue", return_value=queue),
        ):
            await initialize_database()
        factory.assert_called_once_with("configured-url")
        registry.list.assert_awaited_once()
        queue.latest.assert_awaited_once_with("industrial_qa")
        queue.claim.assert_not_called()
        queue.aclose.assert_awaited_once()
        registry.aclose.assert_awaited_once()

    async def test_database_error_propagates_and_connections_are_closed(self):
        registry, queue = AsyncMock(), AsyncMock()
        registry.list.side_effect = RuntimeError("database unavailable")
        with (
            patch("backend.scripts.init_database.server_url_from_env", return_value="configured-url"),
            patch("backend.scripts.init_database.KnowledgeBaseRegistry.from_url", return_value=registry),
            patch("backend.scripts.init_database.JobQueue", return_value=queue),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await initialize_database()
        queue.aclose.assert_awaited_once()
        registry.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
