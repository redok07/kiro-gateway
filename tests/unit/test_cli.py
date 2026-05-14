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


class TestLoadQuotaCache:
    """Tests for _load_quota_cache() function."""

    def test_returns_empty_dict_when_no_state_file(self, tmp_path, monkeypatch):
        """Returns empty dict when state.json doesn't exist."""
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(tmp_path / "nonexistent.json"))
        from kiro.cli import _load_quota_cache
        assert _load_quota_cache() == {}

    def test_returns_empty_dict_when_no_quota_cache_key(self, tmp_path, monkeypatch):
        """Returns empty dict when state.json has no quota_cache key."""
        state_file = tmp_path / "state.json"
        state_file.write_text('{"accounts": {}}', encoding="utf-8")
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(state_file))
        from kiro.cli import _load_quota_cache
        assert _load_quota_cache() == {}

    def test_returns_quota_cache_data(self, tmp_path, monkeypatch):
        """Returns quota_cache dict from state.json."""
        import json
        state_file = tmp_path / "state.json"
        cache_data = {
            "/path/to/account1.json": {
                "totalCredits": 1000,
                "usedCredits": 150,
                "remainingCredits": 850,
                "packageName": "Kiro Pro",
                "lastFetched": 1700000000.0,
            }
        }
        state_file.write_text(json.dumps({"quota_cache": cache_data}), encoding="utf-8")
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(state_file))
        from kiro.cli import _load_quota_cache
        result = _load_quota_cache()
        assert result == cache_data

    def test_returns_empty_dict_on_corrupt_json(self, tmp_path, monkeypatch):
        """Returns empty dict when state.json is corrupt."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json{{{", encoding="utf-8")
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(state_file))
        from kiro.cli import _load_quota_cache
        assert _load_quota_cache() == {}


class TestParseQuotaResponse:
    """Tests for _parse_quota_response() function."""

    def test_empty_usage_list_returns_free(self):
        """Empty usageBreakdownList returns Free with 0 credits."""
        from kiro.cli import _parse_quota_response
        result = _parse_quota_response({"usageBreakdownList": []})
        assert result["_ok"] is True
        assert result["totalCredits"] == 0
        assert result["usedCredits"] == 0
        assert result["packageName"] == "Free"

    def test_parses_pro_account(self):
        """Parses standard Pro account usage."""
        from kiro.cli import _parse_quota_response
        payload = {
            "usageBreakdownList": [{
                "usageLimit": 1000,
                "currentUsage": 150,
            }],
            "subscriptionInfo": {"subscriptionTitle": "Kiro Pro"},
        }
        result = _parse_quota_response(payload)
        assert result["_ok"] is True
        assert result["totalCredits"] == 1000
        assert result["usedCredits"] == 150
        assert result["remainingCredits"] == 850
        assert result["packageName"] == "Kiro Pro"

    def test_parses_with_free_trial_active(self):
        """Includes free trial credits when active."""
        from kiro.cli import _parse_quota_response
        payload = {
            "usageBreakdownList": [{
                "usageLimit": 50,
                "currentUsage": 10,
                "freeTrialInfo": {
                    "freeTrialStatus": "ACTIVE",
                    "usageLimit": 200,
                    "currentUsage": 30,
                },
            }],
            "subscriptionType": "Free",
        }
        result = _parse_quota_response(payload)
        assert result["totalCredits"] == 250  # 50 + 200
        assert result["usedCredits"] == 40    # 10 + 30
        assert result["remainingCredits"] == 210

    def test_parses_with_bonuses(self):
        """Includes bonus credits."""
        from kiro.cli import _parse_quota_response
        payload = {
            "usageBreakdownList": [{
                "usageLimit": 1000,
                "currentUsage": 500,
                "bonuses": [
                    {"usageLimit": 100, "currentUsage": 20},
                    {"usageLimit": 50, "currentUsage": 10},
                ],
            }],
            "subscriptionTitle": "Kiro Pro",
        }
        result = _parse_quota_response(payload)
        assert result["totalCredits"] == 1150  # 1000 + 100 + 50
        assert result["usedCredits"] == 530    # 500 + 20 + 10
        assert result["remainingCredits"] == 620

    def test_remaining_never_negative(self):
        """Remaining credits floor at 0."""
        from kiro.cli import _parse_quota_response
        payload = {
            "usageBreakdownList": [{
                "usageLimit": 100,
                "currentUsage": 150,  # Over limit
            }],
            "subscriptionType": "Free",
        }
        result = _parse_quota_response(payload)
        assert result["remainingCredits"] == 0

    def test_uses_precision_fields(self):
        """Falls back to usageLimitWithPrecision and currentUsageWithPrecision."""
        from kiro.cli import _parse_quota_response
        payload = {
            "usageBreakdownList": [{
                "usageLimitWithPrecision": 1000,
                "currentUsageWithPrecision": 987,
            }],
            "subscriptionInfo": {"subscriptionTitle": "Kiro Pro"},
        }
        result = _parse_quota_response(payload)
        assert result["totalCredits"] == 1000
        assert result["usedCredits"] == 987
        assert result["remainingCredits"] == 13

    def test_no_payload_returns_free(self):
        """None/empty usage list returns Free."""
        from kiro.cli import _parse_quota_response
        result = _parse_quota_response({})
        assert result["_ok"] is True
        assert result["packageName"] == "Free"


class TestShowAccountsListWithQuota:
    """Tests for _show_accounts_list() displaying cached quota."""

    def test_displays_cached_quota(self, tmp_path, capsys, monkeypatch):
        """Shows cached quota next to account name."""
        import json

        # Create credentials.json
        creds_file = tmp_path / "credentials.json"
        creds = [{"type": "json", "path": "/path/to/acct1.json",
                  "comment": "Added by kiro_login.py (alias=testuser-domain)"}]
        creds_file.write_text(json.dumps(creds), encoding="utf-8")

        # Create state.json with quota cache
        state_file = tmp_path / "state.json"
        state_data = {
            "quota_cache": {
                "/path/to/acct1.json": {
                    "totalCredits": 1000,
                    "usedCredits": 200,
                    "remainingCredits": 800,
                    "packageName": "Kiro Pro",
                    "lastFetched": 1700000000.0,
                }
            }
        }
        state_file.write_text(json.dumps(state_data), encoding="utf-8")
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(state_file))

        with patch("kiro.cli._get_account_status", return_value="OK"):
            from kiro.cli import _show_accounts_list
            _show_accounts_list(str(creds_file))

        captured = capsys.readouterr()
        assert "testuser-domain" in captured.out
        assert "200/1000" in captured.out
        assert "Kiro Pro" in captured.out

    def test_no_quota_when_cache_empty(self, tmp_path, capsys, monkeypatch):
        """No quota shown when cache is empty."""
        import json

        creds_file = tmp_path / "credentials.json"
        creds = [{"type": "json", "path": "/path/to/acct1.json",
                  "comment": "Added by kiro_login.py (alias=testuser-domain)"}]
        creds_file.write_text(json.dumps(creds), encoding="utf-8")

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr("kiro.config.ACCOUNTS_STATE_FILE", str(state_file))

        with patch("kiro.cli._get_account_status", return_value="OK"):
            from kiro.cli import _show_accounts_list
            _show_accounts_list(str(creds_file))

        captured = capsys.readouterr()
        assert "testuser-domain" in captured.out
        # No quota numbers should appear
        assert "/1000" not in captured.out
