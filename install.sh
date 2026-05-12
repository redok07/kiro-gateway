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
# Re-running this script upgrades an existing installation safely.
# No pre-requisites needed - works on a fresh VPS out of the box.

set -euo pipefail

REPO="https://github.com/redok07/kiro-gateway.git"
INSTALL_DIR="$HOME/.kiro-gateway"
BIN_DIR="$INSTALL_DIR/bin"
VENV_DIR="$INSTALL_DIR/venv"
ENV_FILE="$INSTALL_DIR/.env"
MIN_PYTHON="3.10"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

info()  { printf "${CYAN}[info]${RESET}  %s\n" "$1"; }
ok()    { printf "${GREEN}[ok]${RESET}    %s\n" "$1"; }
warn()  { printf "${YELLOW}[warn]${RESET}  %s\n" "$1"; }
fail()  { printf "${RED}[error]${RESET} %s\n" "$1"; exit 1; }

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

# --- Install Python ---
install_python() {
    local pkg_mgr="$1"
    local sudo_cmd
    sudo_cmd=$(get_sudo)

    info "Installing Python 3..."
    case "$pkg_mgr" in
        apt)
            $sudo_cmd apt-get update -qq
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
            $sudo_cmd apt-get update -qq
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

# --- Install curl (needed for pip sometimes) ---
install_curl() {
    local pkg_mgr="$1"
    local sudo_cmd
    sudo_cmd=$(get_sudo)

    info "Installing curl..."
    case "$pkg_mgr" in
        apt)
            $sudo_cmd apt-get install -y -qq curl
            ;;
        dnf|yum)
            $sudo_cmd $pkg_mgr install -y -q curl
            ;;
        pacman)
            $sudo_cmd pacman -Sy --noconfirm curl
            ;;
        apk)
            $sudo_cmd apk add --quiet curl
            ;;
        zypper)
            $sudo_cmd zypper install -y -q curl
            ;;
        brew)
            : # curl always available on macOS
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
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
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
            # Need to find the right python3-venv package for the installed version
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

# --- Main ---
main() {
    printf "\n${BOLD}  Kiro Gateway Installer${RESET}\n"
    printf "${DIM}  Cross-platform proxy for Kiro API (Amazon Q Developer)${RESET}\n\n"

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

    # 5. Create or reuse venv
    if [ -f "$VENV_DIR/bin/python" ]; then
        info "Virtual environment exists, reusing..."
    else
        info "Creating virtual environment..."
        "$PYTHON" -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
    fi
    ok "Virtual environment ready"

    # 6. Upgrade pip first (avoid old pip issues on fresh systems)
    info "Ensuring pip is up to date..."
    "$VENV_DIR/bin/python" -m pip install --upgrade --quiet pip 2>/dev/null || true

    # 7. Install/upgrade kiro-gateway
    info "Installing kiro-gateway from GitHub (this may take a minute)..."
    "$VENV_DIR/bin/pip" install --upgrade --quiet "git+${REPO}" || fail "pip install failed. Check your internet connection."

    local installed_ver
    installed_ver=$("$VENV_DIR/bin/kiro-gateway" --version 2>/dev/null || echo "unknown")
    ok "Installed kiro-gateway $installed_ver"

    # 8. Create shell wrapper
    cat > "$BIN_DIR/kiro-gateway" << 'WRAPPER'
#!/usr/bin/env bash
# Kiro Gateway launcher - auto-generated by installer
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$SCRIPT_DIR/venv/bin/kiro-gateway" "$@"
WRAPPER
    chmod +x "$BIN_DIR/kiro-gateway"

    # 9. Create .env if not exists
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << 'ENVFILE'
# Kiro Gateway Configuration
# Documentation: https://github.com/redok07/kiro-gateway#configuration

# Password to protect your proxy (CHANGE THIS!)
PROXY_API_KEY="CHANGE_ME_TO_A_STRONG_RANDOM_STRING"

# Authentication - uncomment ONE method:

# Option 1: Kiro IDE credentials file
# KIRO_CREDS_FILE="~/.aws/sso/cache/kiro-auth-token.json"

# Option 2: Refresh token (from Kiro IDE traffic)
# REFRESH_TOKEN="your_refresh_token_here"

# Option 3: kiro-cli SQLite database
# KIRO_CLI_DB_FILE="~/.local/share/kiro-cli/data.sqlite3"

# Server settings (defaults are fine for most users)
# SERVER_HOST="0.0.0.0"
# SERVER_PORT="8000"

# Debug logging: off | errors | all
# DEBUG_MODE="off"
ENVFILE
        ok "Created $ENV_FILE (edit this before starting!)"
    else
        info ".env already exists, keeping current config"
    fi

    # 10. Add to PATH
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
    # Check if already in PATH
    if echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
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
            ;;
    esac

    # Check if already added to profile
    if [ -f "$profile" ] && grep -qF "$BIN_DIR" "$profile"; then
        info "PATH entry already in $profile"
        return
    fi

    printf "\n# Kiro Gateway\n%s\n" "$export_line" >> "$profile"
    ok "Added to $profile"
}

main "$@"
