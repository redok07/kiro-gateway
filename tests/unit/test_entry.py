# -*- coding: utf-8 -*-

"""
Unit tests for kiro/_entry.py - Package entry point.

Tests CLI argument parsing and command routing.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock


class TestParseArgs:
    """Tests for _parse_args() function."""

    def test_no_args_returns_command_none(self):
        """No subcommand defaults to interactive menu (command=None)."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway"]):
            args = _parse_args()
            assert args.command is None

    def test_serve_command_parsed(self):
        """'serve' subcommand is recognized."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "serve"]):
            args = _parse_args()
            assert args.command == "serve"

    def test_serve_with_host_and_port(self):
        """'serve --host 127.0.0.1 --port 9000' parses correctly."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "serve", "--host", "127.0.0.1", "--port", "9000"]):
            args = _parse_args()
            assert args.command == "serve"
            assert args.host == "127.0.0.1"
            assert args.port == 9000

    def test_serve_short_flags(self):
        """'serve -H 0.0.0.0 -p 8080' parses correctly."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "serve", "-H", "0.0.0.0", "-p", "8080"]):
            args = _parse_args()
            assert args.host == "0.0.0.0"
            assert args.port == 8080

    def test_start_command_parsed(self):
        """'start' subcommand is recognized."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "start"]):
            args = _parse_args()
            assert args.command == "start"

    def test_start_with_port(self):
        """'start --port 3000' parses correctly."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "start", "--port", "3000"]):
            args = _parse_args()
            assert args.command == "start"
            assert args.port == 3000

    def test_stop_command_parsed(self):
        """'stop' subcommand is recognized."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "stop"]):
            args = _parse_args()
            assert args.command == "stop"

    def test_status_command_parsed(self):
        """'status' subcommand is recognized."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "status"]):
            args = _parse_args()
            assert args.command == "status"

    def test_logs_command_default_lines(self):
        """'logs' defaults to 50 lines."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "logs"]):
            args = _parse_args()
            assert args.command == "logs"
            assert args.lines == 50

    def test_logs_command_custom_lines(self):
        """'logs -n 100' sets lines to 100."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "logs", "-n", "100"]):
            args = _parse_args()
            assert args.command == "logs"
            assert args.lines == 100

    def test_setup_path_command_parsed(self):
        """'setup-path' subcommand is recognized."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "setup-path"]):
            args = _parse_args()
            assert args.command == "setup-path"

    def test_version_flag_exits(self):
        """'--version' prints version and exits."""
        from kiro._entry import _parse_args

        with patch.object(sys, "argv", ["kiro-gateway", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                _parse_args()
            assert exc_info.value.code == 0


class TestMainRouting:
    """Tests for main() command routing."""

    def test_no_command_calls_run_cli(self):
        """No subcommand invokes interactive menu."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway"]), \
             patch("kiro.cli.run_cli") as mock_cli:
            main()
            mock_cli.assert_called_once()

    def test_start_calls_start_server(self):
        """'start' invokes start_server with parsed args."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "start", "--port", "4000"]), \
             patch("kiro.cli.start_server") as mock_start:
            main()
            mock_start.assert_called_once_with(host=None, port=4000)

    def test_stop_calls_stop_server(self):
        """'stop' invokes stop_server."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "stop"]), \
             patch("kiro.cli.stop_server") as mock_stop:
            main()
            mock_stop.assert_called_once()

    def test_status_calls_show_status(self):
        """'status' invokes show_status."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "status"]), \
             patch("kiro.cli.show_status") as mock_status:
            main()
            mock_status.assert_called_once()

    def test_logs_calls_view_logs(self):
        """'logs -n 25' invokes view_logs(lines=25)."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "logs", "-n", "25"]), \
             patch("kiro.cli.view_logs") as mock_logs:
            main()
            mock_logs.assert_called_once_with(lines=25)

    def test_setup_path_calls_setup_path(self):
        """'setup-path' invokes setup_path."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "setup-path"]), \
             patch("kiro.cli.setup_path") as mock_setup:
            main()
            mock_setup.assert_called_once()

    def test_serve_calls_run_foreground(self):
        """'serve' invokes _run_foreground."""
        from kiro._entry import main

        with patch.object(sys, "argv", ["kiro-gateway", "serve"]), \
             patch("kiro._entry._run_foreground") as mock_fg:
            main()
            mock_fg.assert_called_once()
