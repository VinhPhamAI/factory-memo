"""Expose native serving and explicit database setup commands."""

# Help must not import resource-owning clients before argument dispatch.
# pylint: disable=import-outside-toplevel

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn


def listener_port(value: str) -> int:
    """Parse a TCP listener port within the usable range.

    Args:
        value: Port text from the command line.
    Returns:
        A port between 1 and 65535.
    Raises:
        argparse.ArgumentTypeError: The value is outside the valid range.
    """
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Port must be an integer.") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535.")
    return port


def main(argv: list[str] | None = None) -> None:
    """Dispatch the installed command without opening resources for help.

    Args:
        argv: Explicit arguments for tests; None reads the process arguments.
    Raises:
        SystemExit: Parsing or database setup fails.
    """
    parser = argparse.ArgumentParser(description="Serve the independent DENSO agent.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the HTTP service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=listener_port, default=8010)
    serve.add_argument("--reload", action="store_true")
    commands.add_parser("setup-db", help="Provision and migrate the agent database.")
    args = parser.parse_args(argv)
    if args.command == "serve":
        options = {
            "factory": True,
            "host": args.host,
            "port": args.port,
            "workers": 1,
            "reload": args.reload,
            "loop": "denso_agent.event_loop:new_event_loop",
            "reload_dirs": (
                [str(Path(__file__).resolve().parent.parent)] if args.reload else None
            ),
        }
        if sys.platform == "win32" and args.reload:
            from denso_agent.windows_reload import WindowsReload

            config = uvicorn.Config("denso_agent.app:create_app", **options)
            server = uvicorn.Server(config)
            socket = config.bind_socket()
            try:
                WindowsReload(config, target=server.run, sockets=[socket]).run()
            finally:
                socket.close()
        else:
            uvicorn.run("denso_agent.app:create_app", **options)
        return
    # Setup imports remain lazy so help needs neither settings nor database access.
    from denso_agent.config import (
        SetupSettings,
    )  # pylint: disable=import-outside-toplevel
    from denso_agent.setup_database import (
        setup_database,
    )  # pylint: disable=import-outside-toplevel

    try:
        from denso_agent.event_loop import (
            new_event_loop,
        )  # pylint: disable=import-outside-toplevel

        with asyncio.Runner(loop_factory=new_event_loop) as runner:
            runner.run(setup_database(SetupSettings()))
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise SystemExit(
            "Database setup failed. Check database credentials and connectivity."
        ) from error


if __name__ == "__main__":
    main()
