# -*- coding: utf-8 -*-

"""
Unit tests for kiro/cli.py - Interactive CLI module.

Tests core functions in isolation without spawning real processes.
"""

import os
import sys
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open


class TestEnsureDataDir:
    """Tests for ensure_data_dir() function."""

    def test_creates_data_dir_and_logs_subdir(self, tmp_path):
        """Verifies both ~/.kiro-gateway/ and logs/ subdirectory are created."""
        with patch("kiro.cli.DATA_DIR", tmp_path / ".kiro-gateway"):
            from kiro.cli import ensure_data_dir
            ensure_data_dir()

            assert (tmp_path / ".kiro-gateway").exists()
            assert (tmp_path / ".kiro-gateway" / "logs").exists()

    def test_idempotent_when_already_exists(self, tmp_path):
        """Calling ensure_data_dir twice does not raise."""
        data_dir = tmp_path / ".kiro-gateway"
        data_dir.mkdir()
        (data_dir / "logs").mkdir()

        with patch("kiro.cli.DATA_DIR", data_dir):
            from kiro.cli import ensure_data_dir
            ensure_data_dir()  # Should not raise
            assert data_dir.exists()


class TestGetServerPid:
    """Tests for get_server_pid() function."""

    def test_returns_none_when_no_pid_file(self, tmp_path):
        """No PID file means no server running."""
        with patch("kiro.cli.PID_FILE", tmp_path / "server.pid"):
            from kiro.cli import get_server_pid
            assert get_server_pid() is None

    def test_returns_none_for_invalid_pid_content(self, tmp_path):
        """Non-numeric PID file content returns None."""
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("not-a-number")

        with patch("kiro.cli.PID_FILE", pid_file):
            from kiro.cli import get_server_pid
            assert get_server_pid() is None

    def test_returns_none_for_dead_process(self, tmp_path):
        """PID file with dead process returns None and cleans up."""
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("99999999")  # Very unlikely to be running

        with patch("kiro.cli.PID_FILE", pid_file), \
             patch("kiro.cli._is_process_alive", return_value=False):
            from kiro.cli import get_server_pid
            assert get_server_pid() is None
            assert not pid_file.exists()  # Stale file cleaned up

    def test_returns_pid_for_running_process(self, tmp_path):
        """PID file with live process returns the PID."""
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("12345")

        with patch("kiro.cli.PID_FILE", pid_file), \
             patch("kiro.cli._is_process_alive", return_value=True):
            from kiro.cli import get_server_pid
            assert get_server_pid() == 12345


class TestIsProcessAlive:
    """Tests for _is_process_alive() function."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_windows_exact_pid_match(self):
        """Windows: PID must match exactly in CSV output (not substring)."""
        from kiro.cli import _is_process_alive

        # Mock tasklist returning PID 4244 - should NOT match PID 42
        mock_result = MagicMock()
        mock_result.stdout = '"python.exe","4244","Console","1","50,000 K"\n'

        with patch("subprocess.run", return_value=mock_result):
            assert _is_process_alive(42) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_windows_exact_pid_found(self):
        """Windows: Exact PID match returns True."""
        from kiro.cli import _is_process_alive

        mock_result = MagicMock()
        mock_result.stdout = '"python.exe","4244","Console","1","50,000 K"\n'

        with patch("subprocess.run", return_value=mock_result):
            assert _is_process_alive(4244) is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
    def test_unix_uses_kill_signal_zero(self):
        """Unix: Uses os.kill(pid, 0) to check process."""
        from kiro.cli import _is_process_alive

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            assert _is_process_alive(1234) is True
            mock_kill.assert_called_once_with(1234, 0)

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
    def test_unix_dead_process_raises_oserror(self):
        """Unix: Dead process raises OSError from os.kill."""
        from kiro.cli import _is_process_alive

        with patch("os.kill", side_effect=ProcessLookupError):
            assert _is_process_alive(99999) is False


class TestStartServer:
    """Tests for start_server() function."""

    def test_refuses_when_already_running(self, tmp_path, capsys):
        """Does not start a second instance if server is already running."""
        with patch("kiro.cli.get_server_pid", return_value=12345):
            from kiro.cli import start_server
            result = start_server()

            assert result is False
            captured = capsys.readouterr()
            assert "already running" in captured.out

    def test_creates_pid_file_on_success(self, tmp_path):
        """Writes PID file after successful process spawn."""
        pid_file = tmp_path / "server.pid"
        log_file = tmp_path / "server.log"
        data_dir = tmp_path / ".kiro-gateway"
        data_dir.mkdir(parents=True)
        (data_dir / "logs").mkdir()

        mock_proc = MagicMock()
        mock_proc.pid = 54321
        mock_proc.poll.return_value = None  # Still running

        with patch("kiro.cli.get_server_pid", return_value=None), \
             patch("kiro.cli.PID_FILE", pid_file), \
             patch("kiro.cli.LOG_FILE", log_file), \
             patch("kiro.cli.DATA_DIR", data_dir), \
             patch("kiro.cli.GATEWAY_ROOT", tmp_path), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch("time.sleep"):
            from kiro.cli import start_server
            result = start_server()

            assert result is True
            assert pid_file.read_text().strip() == "54321"


class TestStopServer:
    """Tests for stop_server() function."""

    def test_reports_not_running_when_no_pid(self, capsys):
        """Prints message when no server is running."""
        with patch("kiro.cli.get_server_pid", return_value=None):
            from kiro.cli import stop_server
            stop_server()

            captured = capsys.readouterr()
            assert "not running" in captured.out.lower()

    def test_terminates_process_and_removes_pid_file(self, tmp_path):
        """Sends terminate signal and cleans up PID file."""
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("12345")

        with patch("kiro.cli.get_server_pid", return_value=12345), \
             patch("kiro.cli.PID_FILE", pid_file), \
             patch("kiro.cli._is_process_alive", side_effect=[True, False]):

            if sys.platform == "win32":
                with patch("subprocess.run") as mock_run:
                    from kiro.cli import stop_server
                    stop_server()
                    # Should call taskkill
                    mock_run.assert_called()
            else:
                with patch("os.kill") as mock_kill:
                    from kiro.cli import stop_server
                    stop_server()
                    mock_kill.assert_called()


class TestViewLogs:
    """Tests for view_logs() function."""

    def test_reports_no_log_file(self, tmp_path, capsys):
        """Prints message when log file doesn't exist."""
        with patch("kiro.cli.LOG_FILE", tmp_path / "nonexistent.log"):
            from kiro.cli import view_logs
            view_logs()

            captured = capsys.readouterr()
            assert "no log" in captured.out.lower() or "not found" in captured.out.lower()

    def test_reads_last_n_lines(self, tmp_path, capsys):
        """Shows last N lines from log file."""
        log_file = tmp_path / "server.log"
        lines = [f"Line {i}\n" for i in range(100)]
        log_file.write_text("".join(lines))

        with patch("kiro.cli.LOG_FILE", log_file):
            from kiro.cli import view_logs
            view_logs(lines=5)

            captured = capsys.readouterr()
            assert "Line 99" in captured.out
            assert "Line 95" in captured.out


class TestClearScreen:
    """Tests for clear_screen() function."""

    def test_calls_cls_on_windows(self):
        """Windows uses 'cls' command."""
        with patch("sys.platform", "win32"), \
             patch("os.system") as mock_system:
            from kiro.cli import clear_screen
            clear_screen()
            mock_system.assert_called_once_with("cls")

    def test_calls_clear_on_unix(self):
        """Unix uses 'clear' command."""
        with patch("sys.platform", "linux"), \
             patch("os.system") as mock_system:
            from kiro.cli import clear_screen
            clear_screen()
            mock_system.assert_called_once_with("clear")


class TestShowStatus:
    """Tests for show_status() function."""

    def test_shows_running_with_pid(self, capsys):
        """Displays running status with PID."""
        with patch("kiro.cli.get_server_pid", return_value=9876):
            from kiro.cli import show_status
            show_status()

            captured = capsys.readouterr()
            assert "9876" in captured.out
            assert "running" in captured.out.lower() or "RUNNING" in captured.out

    def test_shows_stopped_when_no_pid(self, capsys):
        """Displays stopped status."""
        with patch("kiro.cli.get_server_pid", return_value=None):
            from kiro.cli import show_status
            show_status()

            captured = capsys.readouterr()
            assert "stopped" in captured.out.lower() or "STOPPED" in captured.out or "not running" in captured.out.lower()
