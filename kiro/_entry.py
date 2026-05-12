# -*- coding: utf-8 -*-

"""
Kiro Gateway - Package entry point for console_scripts.

This module is the target of the `kiro-gateway` command installed by pip.
It replicates the behavior of `python main.py` (CLI routing).
"""

import sys
import argparse

from kiro.config import APP_VERSION, APP_TITLE, SERVER_HOST, SERVER_PORT, DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


def main() -> None:
    """Entry point for the kiro-gateway command."""
    args = _parse_args()

    if args.command is None:
        from kiro.cli import run_cli
        run_cli()
    elif args.command == "start":
        from kiro.cli import start_server
        start_server(host=args.host, port=args.port)
    elif args.command == "stop":
        from kiro.cli import stop_server
        stop_server()
    elif args.command == "status":
        from kiro.cli import show_status
        show_status()
    elif args.command == "logs":
        from kiro.cli import view_logs
        view_logs(lines=args.lines)
    elif args.command == "setup-path":
        from kiro.cli import setup_path
        setup_path()
    elif args.command == "serve":
        _run_foreground(args)


def _run_foreground(args: argparse.Namespace) -> None:
    """Run the server in foreground mode."""
    import uvicorn
    from main import validate_configuration, _warn_timeout_configuration, resolve_server_config, print_startup_banner, UVICORN_LOG_CONFIG

    validate_configuration()
    _warn_timeout_configuration()
    final_host, final_port = resolve_server_config(args)
    print_startup_banner(final_host, final_port)

    from loguru import logger
    logger.info(f"Starting Uvicorn server on {final_host}:{final_port}...")
    uvicorn.run(
        "main:app",
        host=final_host,
        port=final_port,
        log_config=UVICORN_LOG_CONFIG,
    )


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="kiro-gateway",
        description=f"{APP_TITLE} v{APP_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  kiro-gateway                            # Interactive menu
  kiro-gateway start                      # Start in background
  kiro-gateway start --port 9000          # Start with custom port
  kiro-gateway serve                      # Foreground (old behavior)
  kiro-gateway stop                       # Stop background server
        """
    )

    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command")

    # serve: run in foreground
    serve_parser = subparsers.add_parser("serve", help="Run server in foreground")
    serve_parser.add_argument(
        "-H", "--host", type=str, default=None, metavar="HOST",
        help=f"Server host (default: {DEFAULT_SERVER_HOST})"
    )
    serve_parser.add_argument(
        "-p", "--port", type=int, default=None, metavar="PORT",
        help=f"Server port (default: {DEFAULT_SERVER_PORT})"
    )

    # start: background
    start_parser = subparsers.add_parser("start", help="Start server in background")
    start_parser.add_argument(
        "-H", "--host", type=str, default=None, metavar="HOST",
        help=f"Server host (default: {DEFAULT_SERVER_HOST})"
    )
    start_parser.add_argument(
        "-p", "--port", type=int, default=None, metavar="PORT",
        help=f"Server port (default: {DEFAULT_SERVER_PORT})"
    )

    # stop
    subparsers.add_parser("stop", help="Stop background server")

    # status
    subparsers.add_parser("status", help="Show server status")

    # logs
    logs_parser = subparsers.add_parser("logs", help="View server logs")
    logs_parser.add_argument(
        "-n", "--lines", type=int, default=50,
        help="Number of lines to show (default: 50)"
    )

    # setup-path
    subparsers.add_parser("setup-path", help="Register 'kiro-gateway' command in PATH")

    return parser.parse_args()


if __name__ == "__main__":
    main()
