#!/usr/bin/env bash
# Kiro Gateway - Zero-dependency installer for Linux and macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.sh | bash
#
# This installer is fully autonomous. It will:
#   1. Install Python 3.10+ if not found (via apt/dnf/brew)
#   2. Install git if not found
#   3. Create ~/.kiro-gateway/ with a virtual environment
#   4. Install kiro-gateway from GitHub via pip
#   5. Create a shell wrapper in ~/.kiro-gateway/bin/
#   6. Add ~/.kiro-gateway/bin to your PATH (shell profile)
#   7. Create a starter .env if none exists
#
# Idempotent: re-running this script upgrades safely without data loss.
# No pre-requisites needed - works on a fresh VPS out of the box.

set -euo pipefail

REPO="https://github.com/redok07/kiro-gateway.git"
INSTALL_DIR="$HOME/.kiro-gateway"
BIN_DIR="$INSTALL_DIR/bin"
VENV_DIR="$INSTALL_DIR/venv"
ENV_FILE="$INSTALL_DIR/.env"
LOCK_FILE="$INSTALL_DIR/.install.lock"
MIN_PYTHON="3.10"

# --- Colors (disabled if not a terminal) ---
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' CYAN='' BOLD='' DIM='' RESET=''
fi

info()  { printf "${CYAN}[info]${RESET}  %s\n" "$1"; }
ok()    { printf "${GREEN}[ok]${RESET}    %s\n" "$1"; }
warn()  { printf "${YELLOW}[warn]${RESET}  %s\n" "$1"; }
fail()  { printf "${RED}[error]${RESET} %s\n" "$1"; exit 1; }

# --- Cleanup trap ---
cleanup() {
    local exit_code=$?
    # Remove lock file on exit (success or failure)
    rm -f "$LOCK_FILE" 2>/dev/null || true
    if [ $exit_code -ne 0 ]; then
        printf "\n${RED}[error]${RESET} Installation failed (exit code: $exit_code)\n"
        printf "${DIM}  If this persists, try: rm -rf $INSTALL_DIR && re-run the installer${RESET}\n\n"
    fi
}
trap cleanup EXIT

# --- Acquire install lock (prevent concurrent installs) ---
acquire_lock() {
    mkdir -p "$INSTALL_DIR" 2>/dev/null || true
    if [ -f "$LOCK_FILE" ]; then
        local lock_pid
        lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
            fail "Another installation is running (PID $lock_pid). If stuck, remove $LOCK_FILE"
        fi
        warn "Stale lock file found, removing..."
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
}

# --- Network connectivity check ---
check_network() {
    info "Checking network connectivity..."
    local test_urls=("https://github.com" "https://pypi.org")
    local connected=false

    for url in "${test_urls[@]}"; do
        if command -v curl &>/dev/null; then
            if curl -fsSL --connect-timeout 5 --max-time 10 "$url" -o /dev/null 2>/dev/null; then
                connected=true
                break
            fi
        elif command -v wget &>/dev/null; then
            if wget -q --timeout=5 --spider "$url" 2>/dev/null; then
                connected=true
                break
            fi
        fi
    done

    if [ "$connected" = false ]; then
        # Last resort: try DNS resolution (no network request needed)
        if command -v getent &>/dev/null; then
            if getent hosts github.com &>/dev/null; then
                connected=true
            fi
        elif command -v host &>/dev/null; then
            if host github.com &>/dev/null; then
                connected=true
            fi
        elif command -v ping &>/dev/null; then
            # ping -W is seconds on Linux, milliseconds on macOS - use -t on macOS
            if [ "$(uname -s)" = "Darwin" ]; then
                ping -c 1 -t 3 github.com &>/dev/null && connected=true
            else
                ping -c 1 -W 3 github.com &>/dev/null && connected=true
            fi
        fi
    fi

    if [ "$connected" = false ]; then
        fail "No network connectivity. Cannot reach github.com or pypi.org.
    Check your internet connection or proxy settings (VPN_PROXY_URL)."
    fi
    ok "Network OK"
}

# --- Detect package manager ---
detect_pkg_manager() {
    if command -v apt-get &>/dev/null; then
        echo "apt"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v yum &>/dev/null; then
        echo "yum"
    elif command -v pacman &>/dev/null; then
        echo "pacman"
    elif command -v apk &>/dev/null; then
        echo "apk"
    elif command -v zypper &>/dev/null; then
        echo "zypper"
    elif command -v brew &>/dev/null; then
        echo "brew"
    else
        echo "unknown"
    fi
}

# --- Get sudo command (empty if already root) ---
get_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        echo ""
    elif command -v sudo &>/dev/null; then
        echo "sudo"
    else
        echo ""
    fi
}

# --- Track whether apt-get update has been run this session ---
APT_UPDATED=false

ensure_apt_updated() {
    if [ "$APT_UPDATED" = false ]; then
        local sudo_cmd
        sudo_cmd=$(get_sudo)
        $sudo_cmd apt-get update -qq
        APT_UPDATED=true
    fi
}

# --- Install Python ---
install_python() {
    local pkg_mgr="$1"
    local sudo_cmd
    sudo_cmd=$(get_sudo)

    info "Installing Python 3..."
    case "$pkg_mgr" in
        apt)
            ensure_apt_updated
            $sudo_cmd apt-get install -y -qq python3 python3-venv python3-pip
            ;;
        dnf)
            $sudo_cmd dnf install -y -q python3 python3-pip
            ;;
        yum)
            $sudo_cmd yum install -y -q python3 python3-pip
            ;;
        pacman)
            $sudo_cmd pacman -Sy --noconfirm python python-pip
            ;;
        apk)
            $sudo_cmd apk add --quiet python3 py3-pip python3-dev
            ;;
        zypper)
            $sudo_cmd zypper install -y -q python3 python3-pip python3-venv
            ;;
        brew)
            brew install python@3.12
            ;;
        *)
            fail "Cannot auto-install Python. No supported package manager found.
    Install Python 3.10+ manually: https://www.python.org/downloads/"
            ;;
    esac
}

# --- Install git ---
install_git() {
    local pkg_mgr="$1"
    local sudo_cmd
    sudo_cmd=$(get_sudo)

    info "Installing git..."
    case "$pkg_mgr" in
        apt)
            ensure_apt_updated
            $sudo_cmd apt-get install -y -qq git
            ;;
        dnf)
            $sudo_cmd dnf install -y -q git
            ;;
        yum)
            $sudo_cmd yum install -y -q git
            ;;
        pacman)
            $sudo_cmd pacman -Sy --noconfirm git
            ;;
        apk)
            $sudo_cmd apk add --quiet git
            ;;
        zypper)
            $sudo_cmd zypper install -y -q git
            ;;
        brew)
            brew install git
            ;;
        *)
            fail "Cannot auto-install git. No supported package manager found.
    Install git manually: https://git-scm.com/downloads"
            ;;
    esac
}

# --- Find Python 3.10+ ---
find_python() {
    for cmd in python3 python python3.13 python3.12 python3.11 python3.10; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] 2>/dev/null && [ "$minor" -ge 10 ] 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# --- Check if venv module is available ---
check_venv_module() {
    local python_cmd="$1"
    "$python_cmd" -c "import venv" 2>/dev/null
}

# --- Install venv module if missing ---
install_venv_module() {
    local pkg_mgr="$1"
    local sudo_cmd
    sudo_cmd=$(get_sudo)

    info "Installing python3-venv..."
    case "$pkg_mgr" in
        apt)
            ensure_apt_updated
            local pyver
            pyver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3")
            $sudo_cmd apt-get install -y -qq "python${pyver}-venv" 2>/dev/null || $sudo_cmd apt-get install -y -qq python3-venv
            ;;
        dnf|yum)
            : # venv is included in python3 on Fedora/RHEL
            ;;
        pacman|apk|zypper|brew)
            : # venv is included in python3 on these
            ;;
    esac
}

# --- Validate existing venv is healthy ---
validate_venv() {
    local venv_python="$VENV_DIR/bin/python"

    # Check python binary exists and is executable
    if [ ! -x "$venv_python" ]; then
        return 1
    fi

    # Check it actually runs and has pip
    if ! "$venv_python" -c "import pip; import sys; sys.exit(0)" 2>/dev/null; then
        return 1
    fi

    return 0
}

# --- Generate secure random key (guaranteed 32 chars) ---
generate_api_key() {
    local key=""

    # Try openssl first (most reliable)
    if command -v openssl &>/dev/null; then
        key=$(openssl rand -hex 16 2>/dev/null || echo "")
    fi

    # Fallback to /dev/urandom + od (available on all Unix)
    if [ ${#key} -lt 32 ]; then
        key=$(od -An -tx1 -N16 /dev/urandom 2>/dev/null | tr -d ' \n' || echo "")
    fi

    # Fallback to /dev/urandom + base64 with padding stripped
    if [ ${#key} -lt 32 ]; then
        key=$(head -c 48 /dev/urandom | base64 2>/dev/null | tr -d '/+=\n' || echo "")
    fi

    # Last resort: timestamp + PID based (weak but functional)
    if [ ${#key} -lt 32 ]; then
        key="kg$(date +%s%N)$$$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' ')"
    fi

    # Ensure exactly 32 chars
    printf '%s' "${key:0:32}"
}

# --- Main ---
main() {
    printf "\n${BOLD}  Kiro Gateway Installer${RESET}\n"
    printf "${DIM}  Cross-platform proxy for Kiro API (Amazon Q Developer)${RESET}\n\n"

    # Acquire lock (idempotent: stale locks are cleaned)
    acquire_lock

    # Network check before anything that needs internet
    check_network

    local pkg_mgr
    pkg_mgr=$(detect_pkg_manager)
    info "Detected package manager: $pkg_mgr"

    # 1. Ensure git is available
    if ! command -v git &>/dev/null; then
        warn "git not found, installing..."
        install_git "$pkg_mgr"
        command -v git &>/dev/null || fail "Failed to install git"
        ok "git installed"
    else
        ok "git found: $(git --version)"
    fi

    # 2. Ensure Python 3.10+ is available
    info "Looking for Python >= $MIN_PYTHON..."
    if ! PYTHON=$(find_python); then
        warn "Python 3.10+ not found, installing..."
        install_python "$pkg_mgr"
        PYTHON=$(find_python) || fail "Failed to install Python 3.10+. Install manually: https://www.python.org/downloads/"
    fi

    local pyver
    pyver=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    ok "Found $PYTHON ($pyver)"

    # 3. Ensure venv module is available
    if ! check_venv_module "$PYTHON"; then
        warn "python3-venv not found, installing..."
        install_venv_module "$pkg_mgr"
        check_venv_module "$PYTHON" || fail "Failed to install python3-venv. Install manually:
    Ubuntu/Debian: sudo apt install python3-venv"
    fi

    # 4. Create install directory
    info "Setting up $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR" "$BIN_DIR"

    # 5. Create or reuse venv (with health check)
    if [ -d "$VENV_DIR" ]; then
        if validate_venv; then
            info "Virtual environment exists and is healthy, reusing..."
        else
            warn "Virtual environment is corrupted, recreating..."
            rm -rf "$VENV_DIR"
            "$PYTHON" -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
        fi
    else
        info "Creating virtual environment..."
        "$PYTHON" -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
    fi
    ok "Virtual environment ready"

    # 6. Upgrade pip first (avoid old pip issues on fresh systems)
    info "Ensuring pip is up to date..."
    "$VENV_DIR/bin/python" -m pip install --upgrade --quiet pip 2>/dev/null || warn "pip upgrade failed (non-fatal)"

    # 7. Install/upgrade kiro-gateway
    info "Installing kiro-gateway from GitHub (this may take a minute)..."
    if ! "$VENV_DIR/bin/pip" install --upgrade --quiet "git+${REPO}"; then
        fail "pip install failed. Possible causes:
    - No internet connection
    - git not configured correctly
    - GitHub rate limit (try again in a few minutes)"
    fi

    # Verify installation actually worked
    if [ ! -f "$VENV_DIR/bin/kiro-gateway" ]; then
        fail "Installation appeared to succeed but kiro-gateway binary not found.
    Try: rm -rf $VENV_DIR && re-run the installer"
    fi

    local installed_ver
    installed_ver=$("$VENV_DIR/bin/kiro-gateway" --version 2>/dev/null || echo "unknown")
    ok "Installed kiro-gateway $installed_ver"

    # 8. Create shell wrapper (idempotent: always overwrite)
    cat > "$BIN_DIR/kiro-gateway" << 'WRAPPER'
#!/usr/bin/env bash
# Kiro Gateway launcher - auto-generated by installer
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_BIN="$SCRIPT_DIR/venv/bin/kiro-gateway"
if [ ! -x "$VENV_BIN" ]; then
    echo "[error] kiro-gateway not found at $VENV_BIN" >&2
    echo "        Re-run the installer to fix: curl -fsSL https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.sh | bash" >&2
    exit 1
fi
exec "$VENV_BIN" "$@"
WRAPPER
    chmod +x "$BIN_DIR/kiro-gateway"

    # 9. Create .env if not exists (never overwrite user config)
    if [ ! -f "$ENV_FILE" ]; then
        local api_key
        api_key=$(generate_api_key)

        cat > "$ENV_FILE" << ENVFILE
# Kiro Gateway Configuration
# Documentation: https://github.com/redok07/kiro-gateway#configuration
# Generated by installer - ready to use after adding your auth credential

# ============================================================
# PROXY PASSWORD (auto-generated, use this as your api_key)
# ============================================================
PROXY_API_KEY="${api_key}"

# ============================================================
# AUTHENTICATION - uncomment ONE method below
# ============================================================

# Option 1: Kiro IDE credentials file
# KIRO_CREDS_FILE="~/.aws/sso/cache/kiro-auth-token.json"

# Option 2: Refresh token (from Kiro IDE network traffic)
# REFRESH_TOKEN="your_refresh_token_here"

# Option 3: kiro-cli SQLite database (AWS SSO / Builder ID)
# KIRO_CLI_DB_FILE="~/.local/share/kiro-cli/data.sqlite3"

# ============================================================
# SERVER SETTINGS (pre-configured, no changes needed)
# ============================================================
SERVER_HOST="0.0.0.0"
SERVER_PORT="2507"
DEBUG_MODE="off"
KIRO_REGION="us-east-1"
ENVFILE
        chmod 600 "$ENV_FILE"
        ok "Created $ENV_FILE (permissions: 600)"
        printf "  ${CYAN}Your auto-generated API key:${RESET} ${BOLD}${api_key}${RESET}\n"
        printf "  ${DIM}Use this as api_key/password when connecting clients to the gateway.${RESET}\n"
    else
        info ".env already exists, keeping current config"
    fi

    # 10. Add to PATH (idempotent: checks before adding)
    add_to_path

    # 11. Done
    printf "\n${GREEN}${BOLD}  Installation complete!${RESET}\n\n"
    printf "  ${BOLD}Next steps:${RESET}\n"
    printf "  ${CYAN}1.${RESET} Edit your config:  ${DIM}nano $ENV_FILE${RESET}\n"
    printf "  ${CYAN}2.${RESET} Restart terminal (or run: source ~/.bashrc)\n"
    printf "  ${CYAN}3.${RESET} Start the gateway: ${DIM}kiro-gateway${RESET}\n"
    printf "\n"
    printf "  ${DIM}Or start immediately: $BIN_DIR/kiro-gateway${RESET}\n"
    printf "  ${DIM}Upgrade later:        curl -fsSL https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.sh | bash${RESET}\n"
    printf "  ${DIM}Uninstall:            rm -rf $INSTALL_DIR && remove PATH entry from shell profile${RESET}\n\n"
}

add_to_path() {
    # Check if already in current PATH
    if echo "$PATH" | tr ':' '\n' | grep -qxF "$BIN_DIR"; then
        info "$BIN_DIR already in PATH"
        return
    fi

    local shell_name profile export_line
    shell_name=$(basename "${SHELL:-/bin/bash}")

    case "$shell_name" in
        zsh)
            profile="$HOME/.zshrc"
            export_line="export PATH=\"$BIN_DIR:\$PATH\""
            ;;
        fish)
            profile="$HOME/.config/fish/config.fish"
            export_line="set -gx PATH \"$BIN_DIR\" \$PATH"
            ;;
        *)
            profile="$HOME/.bashrc"
            export_line="export PATH=\"$BIN_DIR:\$PATH\""
            # Also add to .profile for login shells (non-interactive)
            if [ -f "$HOME/.profile" ] && ! grep -qF "$BIN_DIR" "$HOME/.profile" 2>/dev/null; then
                printf "\n# Kiro Gateway\n%s\n" "$export_line" >> "$HOME/.profile"
            fi
            ;;
    esac

    # Check if already added to profile (idempotent)
    if [ -f "$profile" ] && grep -qF "$BIN_DIR" "$profile"; then
        info "PATH entry already in $profile"
        return
    fi

    # Create profile if it doesn't exist (e.g. fresh container)
    if [ "$shell_name" = "fish" ]; then
        mkdir -p "$(dirname "$profile")"
    fi

    printf "\n# Kiro Gateway\n%s\n" "$export_line" >> "$profile"
    ok "Added to $profile"
}

main "$@"
