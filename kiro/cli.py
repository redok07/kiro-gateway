# -*- coding: utf-8 -*-

"""
Kiro Gateway CLI - Interactive command-line interface.

Provides a menu-driven interface for managing the gateway server:
- Start/Stop/Status of the background server process
- Configuration editing (.env)
- Log viewing
- Account management
- First-run setup (data directory, PATH registration)
"""

import os
import sys
import signal
import subprocess
import time
import shutil
from pathlib import Path
from typing import Optional

from kiro.config import APP_VERSION, APP_TITLE, SERVER_HOST, SERVER_PORT, DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


# ANSI color codes
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
WHITE = "\033[97m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Data directory for runtime files
DATA_DIR = Path.home() / ".kiro-gateway"
PID_FILE = DATA_DIR / "server.pid"
LOG_FILE = DATA_DIR / "server.log"
GATEWAY_ROOT = Path(__file__).parent.parent.resolve()


def ensure_data_dir() -> None:
    """Create data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(exist_ok=True)


def get_server_pid() -> Optional[int]:
    """
    Read PID from file and verify the process is still running.

    Returns:
        PID if server is running, None otherwise.
    """
    if not PID_FILE.exists():
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None

    if _is_process_alive(pid):
        return pid

    # Stale PID file - clean up
    PID_FILE.unlink(missing_ok=True)
    return None


def _is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is running."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            return str(pid) in result.stdout
        except OSError:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def start_server(host: Optional[str] = None, port: Optional[int] = None) -> bool:
    """
    Start the gateway server as a background process.

    Args:
        host: Override host (uses config default if None)
        port: Override port (uses config default if None)

    Returns:
        True if started successfully, False otherwise.
    """
    existing_pid = get_server_pid()
    if existing_pid:
        print(f"  {YELLOW}Server is already running (PID: {existing_pid}){RESET}")
        return False

    ensure_data_dir()

    final_host = host or SERVER_HOST or DEFAULT_SERVER_HOST
    final_port = port or SERVER_PORT or DEFAULT_SERVER_PORT

    python_exe = sys.executable
    main_script = str(GATEWAY_ROOT / "main.py")

    cmd = [python_exe, main_script, "serve", "--host", final_host, "--port", str(final_port)]

    log_handle = open(LOG_FILE, "a", encoding="utf-8")

    kwargs = {
        "stdout": log_handle,
        "stderr": log_handle,
        "cwd": str(GATEWAY_ROOT),
    }

    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        print(f"  {RED}Failed to start server: {e}{RESET}")
        log_handle.close()
        return False

    # Give it a moment to start
    time.sleep(1.5)

    if proc.poll() is not None:
        print(f"  {RED}Server exited immediately. Check logs: {LOG_FILE}{RESET}")
        log_handle.close()
        return False

    PID_FILE.write_text(str(proc.pid))
    log_handle.close()

    print(f"  {GREEN}{BOLD}Server started successfully!{RESET}")
    print(f"  {DIM}PID: {proc.pid}{RESET}")
    print(f"  {DIM}URL: http://{final_host}:{final_port}{RESET}")
    print(f"  {DIM}Log: {LOG_FILE}{RESET}")
    return True


def stop_server() -> bool:
    """
    Stop the running gateway server.

    Returns:
        True if stopped successfully, False otherwise.
    """
    pid = get_server_pid()
    if not pid:
        print(f"  {YELLOW}Server is not running.{RESET}")
        return False

    print(f"  {DIM}Stopping server (PID: {pid})...{RESET}")

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            os.kill(pid, signal.SIGTERM)
            # Wait up to 5 seconds for graceful shutdown
            for _ in range(50):
                if not _is_process_alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass

    PID_FILE.unlink(missing_ok=True)
    print(f"  {GREEN}Server stopped.{RESET}")
    return True


def show_status() -> None:
    """Display current server status."""
    pid = get_server_pid()
    if pid:
        print(f"  {GREEN}{BOLD}Server is RUNNING{RESET}")
        print(f"  {DIM}PID: {pid}{RESET}")
        print(f"  {DIM}Log: {LOG_FILE}{RESET}")
    else:
        print(f"  {DIM}Server is STOPPED{RESET}")


def view_logs(lines: int = 50) -> None:
    """
    Display recent server logs.

    Args:
        lines: Number of lines to show from the end.
    """
    if not LOG_FILE.exists():
        print(f"  {DIM}No logs found. Start the server first.{RESET}")
        return

    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

        print(f"  {DIM}--- Last {len(tail)} lines from {LOG_FILE} ---{RESET}")
        print()
        for line in tail:
            print(f"  {line}")
        print()
        print(f"  {DIM}--- End of logs ---{RESET}")
    except OSError as e:
        print(f"  {RED}Failed to read logs: {e}{RESET}")


def configure_env() -> None:
    """Open .env file for editing or create from example."""
    env_file = GATEWAY_ROOT / ".env"
    env_example = GATEWAY_ROOT / ".env.example"

    if not env_file.exists():
        if env_example.exists():
            shutil.copy2(env_example, env_file)
            print(f"  {GREEN}Created .env from .env.example{RESET}")
        else:
            env_file.write_text("# Kiro Gateway Configuration\n# See .env.example for all options\n\nPROXY_API_KEY=\"my-super-secret-password-123\"\n")
            print(f"  {GREEN}Created new .env file{RESET}")

    # Try to open in editor
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    if sys.platform == "win32":
        # Windows: try notepad as fallback
        editor = editor or "notepad"
        try:
            subprocess.Popen([editor, str(env_file)])
            print(f"  {GREEN}Opened {env_file} in {editor}{RESET}")
        except OSError:
            print(f"  {YELLOW}Could not open editor. Edit manually:{RESET}")
            print(f"  {CYAN}{env_file}{RESET}")
    else:
        editor = editor or "nano"
        try:
            subprocess.run([editor, str(env_file)])
        except OSError:
            print(f"  {YELLOW}Could not open editor. Edit manually:{RESET}")
            print(f"  {CYAN}{env_file}{RESET}")


def show_account_menu() -> None:
    """Display account management submenu."""
    from kiro.config import KIRO_CREDS_FILE, KIRO_CLI_DB_FILE, REFRESH_TOKEN, ACCOUNT_SYSTEM

    print()
    print(f"  {WHITE}{BOLD}Account Information{RESET}")
    print()

    if ACCOUNT_SYSTEM:
        print(f"  {GREEN}Mode: Multi-Account System{RESET}")
    elif KIRO_CLI_DB_FILE:
        print(f"  {GREEN}Mode: kiro-cli SQLite{RESET}")
        print(f"  {DIM}DB: {KIRO_CLI_DB_FILE}{RESET}")
    elif KIRO_CREDS_FILE:
        print(f"  {GREEN}Mode: Kiro IDE Credentials File{RESET}")
        print(f"  {DIM}File: {KIRO_CREDS_FILE}{RESET}")
    elif REFRESH_TOKEN:
        print(f"  {GREEN}Mode: Refresh Token (env){RESET}")
        print(f"  {DIM}Token: {REFRESH_TOKEN[:8]}...{RESET}")
    else:
        print(f"  {RED}No credentials configured!{RESET}")
        print(f"  {DIM}Run 'Configure' to set up authentication.{RESET}")

    print()
    input(f"  {DIM}Press Enter to return...{RESET}")


def setup_path() -> bool:
    """
    Create kiro-gateway shim and add to user PATH.

    Returns:
        True if setup was successful.
    """
    ensure_data_dir()
    bin_dir = DATA_DIR / "bin"
    bin_dir.mkdir(exist_ok=True)

    python_exe = sys.executable
    main_cli = str(GATEWAY_ROOT / "main.py")

    if sys.platform == "win32":
        shim_path = bin_dir / "kiro-gateway.cmd"
        shim_content = f'@echo off\r\n"{python_exe}" "{main_cli}" %*\r\n'
        shim_path.write_text(shim_content)

        # Add to user PATH via registry
        _add_to_user_path_windows(str(bin_dir))
    else:
        shim_path = bin_dir / "kiro-gateway"
        shim_content = f'#!/bin/sh\nexec "{python_exe}" "{main_cli}" "$@"\n'
        shim_path.write_text(shim_content)
        shim_path.chmod(0o755)

        # Add to shell profile
        _add_to_user_path_unix(str(bin_dir))

    print(f"  {GREEN}{BOLD}PATH setup complete!{RESET}")
    print(f"  {DIM}Shim: {shim_path}{RESET}")
    print(f"  {YELLOW}Restart your terminal to use 'kiro-gateway' command.{RESET}")
    return True


def _add_to_user_path_windows(bin_dir: str) -> None:
    """Add directory to Windows user PATH via registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE
        )
        try:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path = ""

        if bin_dir.lower() not in current_path.lower():
            new_path = f"{current_path};{bin_dir}" if current_path else bin_dir
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            # Broadcast WM_SETTINGCHANGE so running shells pick it up
            try:
                import ctypes
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 0x0002, 5000, None
                )
            except Exception:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"  {YELLOW}Could not update PATH automatically: {e}{RESET}")
        print(f"  {DIM}Add this to your PATH manually: {bin_dir}{RESET}")


def _add_to_user_path_unix(bin_dir: str) -> None:
    """Add directory to shell profile on Unix."""
    export_line = f'\nexport PATH="{bin_dir}:$PATH"\n'

    # Detect shell profile
    shell = os.environ.get("SHELL", "/bin/bash")
    if "zsh" in shell:
        profile = Path.home() / ".zshrc"
    elif "fish" in shell:
        profile = Path.home() / ".config" / "fish" / "config.fish"
        export_line = f'\nset -gx PATH "{bin_dir}" $PATH\n'
    else:
        profile = Path.home() / ".bashrc"

    try:
        content = profile.read_text() if profile.exists() else ""
        if bin_dir not in content:
            with open(profile, "a") as f:
                f.write(export_line)
            print(f"  {DIM}Added to {profile}{RESET}")
    except OSError as e:
        print(f"  {YELLOW}Could not update {profile}: {e}{RESET}")
        print(f"  {DIM}Add this to your PATH manually: {bin_dir}{RESET}")


def print_menu() -> None:
    """Print the main interactive menu."""
    pid = get_server_pid()
    status = f"{GREEN}RUNNING{RESET}" if pid else f"{DIM}STOPPED{RESET}"

    print()
    print(f"  {WHITE}{BOLD}  {APP_TITLE} v{APP_VERSION}{RESET}")
    print(f"  {DIM}  Server: {status}")
    print()
    print(f"  {CYAN}1{RESET}  Start server")
    print(f"  {CYAN}2{RESET}  Stop server")
    print(f"  {CYAN}3{RESET}  Server status")
    print(f"  {CYAN}4{RESET}  Configure (.env)")
    print(f"  {CYAN}5{RESET}  View logs")
    print(f"  {CYAN}6{RESET}  Account info")
    print(f"  {CYAN}7{RESET}  Setup PATH (kiro-gateway command)")
    print(f"  {CYAN}0{RESET}  Exit")
    print()


def run_cli() -> None:
    """Main CLI loop."""
    ensure_data_dir()

    # First-run check
    shim_exists = (DATA_DIR / "bin" / ("kiro-gateway.cmd" if sys.platform == "win32" else "kiro-gateway")).exists()
    if not shim_exists:
        print()
        print(f"  {WHITE}{BOLD}First-time setup{RESET}")
        print(f"  {DIM}Setting up 'kiro-gateway' command...{RESET}")
        setup_path()
        print()

    while True:
        print_menu()
        try:
            choice = input(f"  {WHITE}>{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        print()

        if choice == "1":
            start_server()
        elif choice == "2":
            stop_server()
        elif choice == "3":
            show_status()
        elif choice == "4":
            configure_env()
        elif choice == "5":
            view_logs()
        elif choice == "6":
            show_account_menu()
        elif choice == "7":
            setup_path()
        elif choice == "0":
            break
        else:
            print(f"  {DIM}Invalid choice.{RESET}")
