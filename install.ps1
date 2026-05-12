# Kiro Gateway - One-line installer for Windows
# Usage: irm https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.ps1 | iex
#
# What this does:
#   1. Checks Python 3.10+ is available
#   2. Creates ~/.kiro-gateway/ with a virtual environment
#   3. Installs kiro-gateway from GitHub via pip
#   4. Creates a .cmd wrapper in ~/.kiro-gateway/bin/
#   5. Adds ~/.kiro-gateway/bin to your user PATH
#   6. Creates a starter .env if none exists
#
# Re-running this script upgrades an existing installation safely.

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/redok07/kiro-gateway.git"
$InstallDir = Join-Path $env:USERPROFILE ".kiro-gateway"
$BinDir = Join-Path $InstallDir "bin"
$VenvDir = Join-Path $InstallDir "venv"
$EnvFile = Join-Path $InstallDir ".env"
$MinPython = "3.10"

# --- Helpers ---
function Write-Info  { param($Msg) Write-Host "  [info]  $Msg" -ForegroundColor Cyan }
function Write-Ok    { param($Msg) Write-Host "  [ok]    $Msg" -ForegroundColor Green }
function Write-Warn  { param($Msg) Write-Host "  [warn]  $Msg" -ForegroundColor Yellow }
function Write-Fail  { param($Msg) Write-Host "  [error] $Msg" -ForegroundColor Red; throw $Msg }

function Find-Python {
    $candidates = @("python", "python3", "py")
    foreach ($cmd in $candidates) {
        try {
            $output = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) {
                $parts = $output.Split(".")
                $major = [int]$parts[0]
                $minor = [int]$parts[1]
                if ($major -ge 3 -and $minor -ge 10) {
                    return $cmd
                }
            }
        } catch {
            continue
        }
    }

    # Try py launcher with version flag
    try {
        $output = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            $parts = $output.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                return "py -3"
            }
        }
    } catch {}

    return $null
}

function Add-ToUserPath {
    param([string]$Dir)

    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -and $currentPath.ToLower().Contains($Dir.ToLower())) {
        Write-Info "$Dir already in PATH"
        return
    }

    $newPath = if ($currentPath) { "$currentPath;$Dir" } else { $Dir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

    # Broadcast WM_SETTINGCHANGE
    try {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinEnv {
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
        uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
}
"@ -ErrorAction SilentlyContinue
        $result = [UIntPtr]::Zero
        [WinEnv]::SendMessageTimeout([IntPtr]0xFFFF, 0x001A, [UIntPtr]::Zero, "Environment", 0x0002, 5000, [ref]$result) | Out-Null
    } catch {}

    # Update current session
    $env:Path = "$Dir;$env:Path"
    Write-Ok "Added to user PATH"
}

# --- Main ---
function Main {
    Write-Host ""
    Write-Host "  Kiro Gateway Installer" -ForegroundColor White
    Write-Host "  Cross-platform proxy for Kiro API (Amazon Q Developer)" -ForegroundColor DarkGray
    Write-Host ""

    # 1. Find Python
    Write-Info "Looking for Python >= $MinPython..."
    $python = Find-Python
    if (-not $python) {
        Write-Fail "Python 3.10+ not found. Install from: https://www.python.org/downloads/`nMake sure to check 'Add Python to PATH' during installation."
    }

    $pyver = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    Write-Ok "Found $python ($pyver)"

    # 2. Create install directory
    Write-Info "Setting up $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null

    # 3. Create or reuse venv
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (Test-Path $venvPython) {
        Write-Info "Virtual environment exists, reusing..."
    } else {
        Write-Info "Creating virtual environment..."
        & $python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to create virtual environment."
        }
    }
    Write-Ok "Virtual environment ready"

    # 4. Install/upgrade kiro-gateway
    Write-Info "Installing kiro-gateway from GitHub (this may take a minute)..."
    $pip = Join-Path $VenvDir "Scripts\pip.exe"
    & $pip install --upgrade --quiet "git+$Repo"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed. Check your internet connection and that git is installed."
    }

    $kiroExe = Join-Path $VenvDir "Scripts\kiro-gateway.exe"
    $installedVer = "unknown"
    if (Test-Path $kiroExe) {
        try {
            $installedVer = & $kiroExe --version 2>$null
        } catch {}
    }
    Write-Ok "Installed kiro-gateway $installedVer"

    # 5. Create .cmd wrapper
    $wrapperPath = Join-Path $BinDir "kiro-gateway.cmd"
    $wrapperContent = "@echo off`r`n`"%~dp0\..\venv\Scripts\kiro-gateway.exe`" %*"
    Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding ASCII

    # Also create a ps1 wrapper for PowerShell users
    $ps1Wrapper = Join-Path $BinDir "kiro-gateway.ps1"
    $ps1Content = @'
# Kiro Gateway launcher - auto-generated by installer
$exe = Join-Path $PSScriptRoot "..\venv\Scripts\kiro-gateway.exe"
& $exe @args
'@
    Set-Content -Path $ps1Wrapper -Value $ps1Content -Encoding UTF8

    # 6. Create .env if not exists
    if (-not (Test-Path $EnvFile)) {
        $envContent = @'
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
'@
        Set-Content -Path $EnvFile -Value $envContent -Encoding UTF8
        Write-Ok "Created $EnvFile (edit this before starting!)"
    } else {
        Write-Info ".env already exists, keeping current config"
    }

    # 7. Add to PATH
    Add-ToUserPath -Dir $BinDir

    # 8. Done
    Write-Host ""
    Write-Host "  Installation complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Host "  1. Edit your config:  " -NoNewline; Write-Host "notepad $EnvFile" -ForegroundColor DarkGray
    Write-Host "  2. Restart terminal (PATH updated)"
    Write-Host "  3. Start the gateway: " -NoNewline; Write-Host "kiro-gateway" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Or start immediately: $BinDir\kiro-gateway.cmd" -ForegroundColor DarkGray
    Write-Host "  Upgrade later:        irm https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.ps1 | iex" -ForegroundColor DarkGray
    Write-Host "  Uninstall:            Remove-Item -Recurse $InstallDir" -ForegroundColor DarkGray
    Write-Host ""
}

Main
