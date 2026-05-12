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
            # Match exact PID in CSV output (avoid substring matches like 42 in 4244)
            for line in result.stdout.splitlines():
                fields = line.strip().strip('"').split('","')
                if len(fields) >= 2 and fields[1] == str(pid):
                    return True
            return False
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

    final_host = host if host is not None else (SERVER_HOST or DEFAULT_SERVER_HOST)
    final_port = port if port is not None else (SERVER_PORT or DEFAULT_SERVER_PORT)

    python_exe = sys.executable

    # Use module invocation so it works both in dev (python main.py) and pip-installed mode
    cmd = [python_exe, "-m", "kiro._entry", "serve", "--host", final_host, "--port", str(final_port)]

    # Determine working directory: project root if dev, ~/.kiro-gateway if pip-installed
    if (GATEWAY_ROOT / "main.py").exists():
        work_dir = str(GATEWAY_ROOT)
    else:
        work_dir = str(DATA_DIR)

    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "cwd": work_dir,
    }

    # Redirect stdout/stderr to log file
    try:
        log_handle = open(LOG_FILE, "a", encoding="utf-8")
    except OSError as e:
        print(f"  {RED}Failed to open log file: {e}{RESET}")
        return False

    kwargs["stdout"] = log_handle
    kwargs["stderr"] = log_handle

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

    # Close our handle - the child process has its own copy of the fd
    log_handle.close()

    # Give it a moment to start and check it didn't crash immediately
    time.sleep(1.5)

    if proc.poll() is not None:
        exit_code = proc.returncode
        print(f"  {RED}Server exited immediately (code: {exit_code}). Check logs:{RESET}")
        print(f"  {DIM}{LOG_FILE}{RESET}")
        return False

    PID_FILE.write_text(str(proc.pid))

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
    Display recent server logs (reads only tail, memory-safe for large files).

    Args:
        lines: Number of lines to show from the end.
    """
    if not LOG_FILE.exists():
        print(f"  {DIM}No logs found. Start the server first.{RESET}")
        return

    try:
        # Read only the tail to avoid loading huge log files into memory
        with open(LOG_FILE, "rb") as f:
            # Seek from end, read last ~64KB max
            try:
                f.seek(0, 2)
                file_size = f.tell()
                read_size = min(file_size, 65536)
                f.seek(-read_size, 2)
            except OSError:
                f.seek(0)
            chunk = f.read().decode("utf-8", errors="replace")

        all_lines = chunk.splitlines()
        tail = all_lines[-lines:]

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
    # Priority: CWD .env (dev) > ~/.kiro-gateway/.env (pip-installed) > project root
    cwd_env = Path.cwd() / ".env"
    home_env = DATA_DIR / ".env"
    project_env = GATEWAY_ROOT / ".env"
    env_example = GATEWAY_ROOT / ".env.example"

    if cwd_env.exists():
        env_file = cwd_env
    elif home_env.exists():
        env_file = home_env
    elif project_env.exists():
        env_file = project_env
    else:
        # Create in ~/.kiro-gateway/ for pip-installed, or CWD if project root exists
        if GATEWAY_ROOT.exists() and (GATEWAY_ROOT / "main.py").exists():
            env_file = project_env
        else:
            env_file = home_env

        if env_example.exists():
            shutil.copy2(env_example, env_file)
            print(f"  {GREEN}Created .env from .env.example{RESET}")
        else:
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text("# Kiro Gateway Configuration\n# See https://github.com/redok07/kiro-gateway#configuration\n\nPROXY_API_KEY=\"my-super-secret-password-123\"\nSERVER_HOST=\"0.0.0.0\"\nSERVER_PORT=\"2507\"\n")
            print(f"  {GREEN}Created new .env file{RESET}")

    print(f"  {DIM}Config location: {env_file}{RESET}")

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
    """Display account management submenu with credential details and login option."""
    from kiro.config import (
        KIRO_CREDS_FILE, KIRO_CLI_DB_FILE, REFRESH_TOKEN,
        ACCOUNT_SYSTEM, PROFILE_ARN, REGION, ACCOUNTS_CONFIG_FILE,
    )

    clear_screen()
    print()
    print(f"  {WHITE}{BOLD}Account Information{RESET}")
    print()

    # Determine auth mode and show details
    auth_mode = "Not configured"
    cred_details: list = []

    if ACCOUNT_SYSTEM:
        auth_mode = "Multi-Account System"
        cred_details.append(("Failover", "Enabled"))
        # Show accounts from credentials.json
        _show_accounts_list(ACCOUNTS_CONFIG_FILE)
    elif KIRO_CLI_DB_FILE:
        auth_mode = "kiro-cli SQLite (AWS SSO)"
        cred_details.append(("Database", KIRO_CLI_DB_FILE))
        _add_sqlite_details(KIRO_CLI_DB_FILE, cred_details)
    elif KIRO_CREDS_FILE:
        auth_mode = "Kiro IDE Credentials File"
        cred_details.append(("File", KIRO_CREDS_FILE))
        _add_creds_file_details(KIRO_CREDS_FILE, cred_details)
    elif REFRESH_TOKEN:
        auth_mode = "Refresh Token (env)"
        cred_details.append(("Token", f"{REFRESH_TOKEN[:8]}...{REFRESH_TOKEN[-4:]}"))

    # Common fields
    if REGION:
        cred_details.append(("SSO Region", REGION))
    if PROFILE_ARN:
        cred_details.append(("Profile ARN", PROFILE_ARN))

    # Display
    if auth_mode == "Not configured":
        print(f"  {RED}No credentials configured!{RESET}")
        print(f"  {DIM}Use option [L] below to login, or [4] Configure to set up manually.{RESET}")
    else:
        print(f"  {GREEN}Auth Mode:{RESET}  {auth_mode}")
        for label, value in cred_details:
            print(f"  {DIM}{label}:{RESET}  {value}")

    print()
    print(f"  {CYAN}L{RESET}  Login (Google OAuth via Camoufox)")
    print(f"  {CYAN}T{RESET}  Test connection")
    print(f"  {CYAN}0{RESET}  Back to main menu")
    print()

    try:
        choice = input(f"  {WHITE}>{RESET} ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    if choice == "l":
        print()
        run_login_flow()
    elif choice == "t":
        print()
        test_connection()
    # "0" or anything else returns to main menu


def _show_accounts_list(config_path: str) -> None:
    """Read and display accounts from credentials.json with alias and status."""
    import json
    from pathlib import Path

    path = Path(config_path)
    if not path.is_absolute():
        # Check multiple locations: CWD, project root, data dir
        candidates = [
            Path.cwd() / path,
            GATEWAY_ROOT / path,
            DATA_DIR / path,
        ]
        path = next((p for p in candidates if p.exists()), GATEWAY_ROOT / path)

    if not path.exists():
        print(f"  {DIM}No credentials.json found at {path}{RESET}")
        return

    try:
        accounts = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(accounts, list) or not accounts:
            print(f"  {DIM}No accounts in credentials.json{RESET}")
            return
    except Exception as e:
        print(f"  {RED}Error reading credentials.json: {e}{RESET}")
        return

    print()
    print(f"  {WHITE}Accounts ({len(accounts)}):{RESET}")

    for i, entry in enumerate(accounts, 1):
        if not isinstance(entry, dict):
            continue

        # Extract alias from comment or path
        comment = entry.get("comment", "")
        alias = ""
        if "alias=" in comment:
            alias = comment.split("alias=")[-1].rstrip(")")
        if not alias:
            # Derive from path filename
            entry_path = entry.get("path", "")
            if entry_path:
                fname = Path(entry_path).stem
                alias = fname.replace("kiro-auto-", "")

        entry_type = entry.get("type", "unknown")
        entry_path = entry.get("path", "")

        # Check if credential file exists and token status
        status = _get_account_status(entry_path)

        print(f"    {CYAN}{i}.{RESET} {WHITE}{alias or 'unnamed'}{RESET}"
              f"  {DIM}({entry_type}){RESET}  {status}")


def _get_account_status(file_path: str) -> str:
    """Check credential file existence and token expiry."""
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    if not file_path:
        return f"{RED}no path{RESET}"

    path = Path(file_path)
    if not path.exists():
        return f"{RED}file missing{RESET}"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expires_at = data.get("expiresAt", "")
        if not expires_at:
            return f"{YELLOW}no expiry info{RESET}"

        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        remaining = exp - now

        if remaining.total_seconds() <= 0:
            return f"{RED}EXPIRED{RESET}"
        elif remaining.total_seconds() < 600:
            return f"{YELLOW}{int(remaining.total_seconds() / 60)}m left{RESET}"
        else:
            hours = remaining.total_seconds() / 3600
            return f"{GREEN}{hours:.1f}h left{RESET}"
    except Exception:
        return f"{YELLOW}unknown{RESET}"


def _add_sqlite_details(db_path: str, details: list) -> None:
    """Read credential metadata from kiro-cli SQLite database."""
    import json
    import sqlite3
    from pathlib import Path

    path = Path(db_path).expanduser()
    if not path.exists():
        details.append(("Status", f"{RED}Database file not found{RESET}"))
        return

    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()

        # Find which token key is active
        token_keys = [
            ("kirocli:social:token", "Social Login (Google/GitHub/Microsoft)"),
            ("kirocli:odic:token", "AWS SSO OIDC"),
            ("codewhisperer:odic:token", "Legacy AWS SSO OIDC"),
        ]

        for key, label in token_keys:
            cursor.execute("SELECT value FROM auth_kv WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                details.append(("Auth Type", label))
                try:
                    data = json.loads(row[0])
                    if "region" in data:
                        details.append(("Token Region", data["region"]))
                    if "expires_at" in data and data["expires_at"]:
                        details.append(("Expires At", _format_expiry(data["expires_at"])))
                    if "startUrl" in data:
                        details.append(("Start URL", data["startUrl"]))
                except (json.JSONDecodeError, KeyError):
                    pass
                break
        else:
            details.append(("Status", f"{YELLOW}No token found in database{RESET}"))

        conn.close()
    except Exception as e:
        details.append(("Status", f"{RED}Error reading DB: {e}{RESET}"))


def _add_creds_file_details(file_path: str, details: list) -> None:
    """Read credential metadata from JSON credentials file."""
    import json
    from pathlib import Path

    path = Path(file_path).expanduser()
    if not path.exists():
        details.append(("Status", f"{RED}File not found{RESET}"))
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "clientId" in data and "clientSecret" in data:
            details.append(("Auth Type", "AWS SSO OIDC"))
        elif "clientIdHash" in data:
            details.append(("Auth Type", "Enterprise Kiro IDE"))
        else:
            details.append(("Auth Type", "Kiro Desktop"))

        if "region" in data:
            details.append(("Token Region", data["region"]))
        if "expiresAt" in data:
            details.append(("Expires At", _format_expiry(data["expiresAt"])))
        if "profileArn" in data:
            details.append(("Profile ARN", data["profileArn"]))
    except Exception as e:
        details.append(("Status", f"{RED}Error reading file: {e}{RESET}"))


def _format_expiry(expiry_str: str) -> str:
    """Format expiry timestamp with color based on remaining time."""
    from datetime import datetime, timezone

    try:
        # Handle ISO 8601 format
        exp = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        remaining = exp - now

        if remaining.total_seconds() <= 0:
            return f"{RED}EXPIRED{RESET} ({expiry_str})"
        elif remaining.total_seconds() < 600:
            mins = int(remaining.total_seconds() / 60)
            return f"{YELLOW}Expires in {mins}m{RESET}"
        else:
            hours = remaining.total_seconds() / 3600
            if hours < 1:
                return f"{GREEN}{int(remaining.total_seconds() / 60)}m remaining{RESET}"
            return f"{GREEN}{hours:.1f}h remaining{RESET}"
    except (ValueError, TypeError):
        return str(expiry_str)


def test_connection() -> None:
    """Test if the gateway can reach Kiro API with current credentials."""
    import asyncio
    from kiro.config import REGION

    print(f"  {DIM}Testing connection to Kiro API (region: {REGION})...{RESET}")

    async def _test() -> bool:
        try:
            from kiro.auth import KiroAuthManager
            from kiro.config import REFRESH_TOKEN, KIRO_CREDS_FILE, KIRO_CLI_DB_FILE

            auth = KiroAuthManager(
                refresh_token=REFRESH_TOKEN or None,
                creds_file=KIRO_CREDS_FILE or None,
                sqlite_db=KIRO_CLI_DB_FILE or None,
                region=REGION,
            )
            token = await auth.get_access_token()
            if token:
                print(f"  {GREEN}Connection successful!{RESET}")
                print(f"  {DIM}Access token obtained ({len(token)} chars){RESET}")
                return True
            else:
                print(f"  {RED}Failed: No access token returned{RESET}")
                return False
        except Exception as e:
            print(f"  {RED}Failed: {e}{RESET}")
            return False

    try:
        asyncio.run(_test())
    except Exception as e:
        print(f"  {RED}Error: {e}{RESET}")

    print()
    input(f"  {DIM}Press Enter to continue...{RESET}")


def run_login_flow() -> None:
    """
    Interactive login via kiro_login.py (Google OAuth PKCE with Camoufox).

    Wraps the existing tools/kiro_login.py which handles:
    - Google OAuth PKCE login via anti-detect browser (Camoufox)
    - Token validation against AWS Q API
    - Saving credentials in Kiro IDE-compatible JSON format
    - Optional registration in credentials.json for multi-account system
    """
    import getpass

    print(f"  {WHITE}{BOLD}Login via Google Account (Kiro OAuth){RESET}")
    print()
    print(f"  {DIM}Uses Camoufox browser to automate Google login.{RESET}")
    print(f"  {DIM}Requires: pip install camoufox browserforge aiohttp{RESET}")
    print()

    # Check if kiro_login.py dependencies are available
    login_script = GATEWAY_ROOT / "tools" / "kiro_login.py"
    if not login_script.exists():
        print(f"  {RED}Login script not found: {login_script}{RESET}")
        print(f"  {DIM}Make sure tools/kiro_login.py exists in the project.{RESET}")
        return

    # Get email
    try:
        email = input(f"  Google email: ").strip()
    except (KeyboardInterrupt, EOFError):
        return
    if not email:
        print(f"  {RED}Email is required.{RESET}")
        return

    # Get password (from env or prompt)
    password = os.environ.get("KIRO_LOGIN_PASSWORD", "")
    if not password:
        try:
            password = getpass.getpass(f"  Google password: ")
        except (KeyboardInterrupt, EOFError):
            return
    if not password:
        print(f"  {RED}Password is required.{RESET}")
        print(f"  {DIM}Tip: set KIRO_LOGIN_PASSWORD env var for automation.{RESET}")
        return

    # Options
    print()
    print(f"  {DIM}Options:{RESET}")
    print(f"  {CYAN}1{RESET}  Headless (default, faster)")
    print(f"  {CYAN}2{RESET}  Visible browser (for captcha/2FA)")
    print()
    try:
        mode_choice = input(f"  Mode [1]: ").strip()
    except (KeyboardInterrupt, EOFError):
        return
    headless = mode_choice != "2"

    # Auto-register in credentials.json when account system is active
    from kiro.config import ACCOUNT_SYSTEM
    register = bool(ACCOUNT_SYSTEM)

    print()
    print(f"  {DIM}Starting login for {email}...{RESET}")
    print(f"  {DIM}Mode: {'headless' if headless else 'visible browser'}{RESET}")
    print()

    # Build command - password passed via env var to avoid exposure in process list
    cmd = [
        sys.executable, str(login_script),
        "login",
        "--email", email,
    ]
    if not headless:
        cmd.append("--no-headless")
    if register:
        cmd.append("--register")

    # Run the login script with password in environment (not visible in ps/tasklist)
    env = os.environ.copy()
    env["KIRO_LOGIN_PASSWORD"] = password

    try:
        result = subprocess.run(
            cmd,
            cwd=str(GATEWAY_ROOT),
            capture_output=False,
            text=True,
            env=env,
        )
        if result.returncode == 0:
            print()
            print(f"  {GREEN}{BOLD}Login successful!{RESET}")
            print(f"  {DIM}Credentials saved. You may need to update .env to point to the file.{RESET}")
        else:
            print()
            print(f"  {RED}Login failed (exit code: {result.returncode}){RESET}")
            if result.returncode == 2:
                print(f"  {DIM}Missing dependencies? Run:{RESET}")
                print(f"  {CYAN}  pip install camoufox browserforge aiohttp{RESET}")
    except FileNotFoundError:
        print(f"  {RED}Python executable not found: {sys.executable}{RESET}")
    except Exception as e:
        print(f"  {RED}Error running login: {e}{RESET}")

    print()
    input(f"  {DIM}Press Enter to continue...{RESET}")


def uninstall_gateway() -> None:
    """
    Uninstall kiro-gateway: remove data directory, PATH entry, and optionally pip package.

    Removes:
    - ~/.kiro-gateway/ (PID file, logs, bin shim)
    - PATH entry from Windows registry or shell profile
    - pip package (optional, user-confirmed)
    """
    clear_screen()
    print()
    print(f"  {RED}{BOLD}Uninstall Kiro Gateway{RESET}")
    print()
    print(f"  {DIM}This will remove:{RESET}")
    print(f"    - {DATA_DIR} (data, logs, shim)")
    print(f"    - PATH entry for 'kiro-gateway' command")
    print()

    try:
        confirm = input(f"  {YELLOW}Are you sure? [y/N]: {RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return
    if confirm != "y":
        print(f"  {DIM}Cancelled.{RESET}")
        return

    # Stop server if running
    pid = get_server_pid()
    if pid:
        print(f"  {DIM}Stopping running server (PID {pid})...{RESET}")
        stop_server()

    # Remove PATH entry
    bin_dir = str(DATA_DIR / "bin")
    if sys.platform == "win32":
        _remove_from_user_path_windows(bin_dir)
    else:
        _remove_from_user_path_unix(bin_dir)

    # Remove data directory
    if DATA_DIR.exists():
        try:
            shutil.rmtree(DATA_DIR)
            print(f"  {GREEN}Removed {DATA_DIR}{RESET}")
        except OSError as e:
            print(f"  {YELLOW}Could not fully remove {DATA_DIR}: {e}{RESET}")

    # Ask about pip uninstall
    print()
    try:
        pip_confirm = input(f"  {DIM}Also run 'pip uninstall kiro-gateway'? [y/N]: {RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        pip_confirm = "n"

    if pip_confirm == "y":
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "kiro-gateway", "-y"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"  {GREEN}pip package removed.{RESET}")
            else:
                print(f"  {DIM}pip uninstall returned: {result.stderr.strip() or 'not installed'}{RESET}")
        except Exception as e:
            print(f"  {YELLOW}pip uninstall failed: {e}{RESET}")

    print()
    print(f"  {GREEN}{BOLD}Uninstall complete.{RESET}")
    print(f"  {DIM}Project source at {GATEWAY_ROOT} was NOT removed.{RESET}")
    print(f"  {DIM}Credential files (~/.aws/sso/cache/) were NOT removed.{RESET}")


def _remove_from_user_path_windows(bin_dir: str) -> None:
    """Remove directory from Windows user PATH via registry."""
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
            winreg.CloseKey(key)
            return

        # Filter out our bin_dir
        parts = [p for p in current_path.split(";") if p.strip().lower() != bin_dir.lower()]
        new_path = ";".join(parts)

        if new_path != current_path:
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            print(f"  {GREEN}Removed from PATH (registry){RESET}")
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
        print(f"  {YELLOW}Could not remove from PATH: {e}{RESET}")


def _remove_from_user_path_unix(bin_dir: str) -> None:
    """Remove PATH export line from shell profiles."""
    export_line = f'export PATH="{bin_dir}:$PATH"'
    profiles = [
        Path.home() / ".bashrc",
        Path.home() / ".zshrc",
        Path.home() / ".config" / "fish" / "config.fish",
        Path.home() / ".profile",
    ]
    for profile in profiles:
        if not profile.exists():
            continue
        try:
            content = profile.read_text()
            if bin_dir in content:
                lines = [l for l in content.splitlines() if bin_dir not in l]
                profile.write_text("\n".join(lines) + "\n")
                print(f"  {GREEN}Removed from {profile}{RESET}")
        except OSError:
            pass




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


def clear_screen() -> None:
    """Clear terminal screen."""
    os.system("cls" if sys.platform == "win32" else "clear")


def print_menu() -> None:
    """Clear screen and print the main interactive menu."""
    clear_screen()

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
    print(f"  {CYAN}6{RESET}  Account info & Login")
    print(f"  {CYAN}7{RESET}  Setup PATH (kiro-gateway command)")
    print(f"  {CYAN}8{RESET}  Uninstall")
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

        if choice == "0":
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
        elif choice == "8":
            uninstall_gateway()
            break  # Exit after uninstall
        else:
            print(f"  {DIM}Invalid choice.{RESET}")

        # Pause so user can read the output before screen clears
        if choice not in ("6", "8"):  # Account menu and uninstall have their own flow
            print()
            input(f"  {DIM}Press Enter to continue...{RESET}")
