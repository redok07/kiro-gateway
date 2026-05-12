# Kiro Gateway - Zero-dependency installer for Windows
# Usage: irm https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.ps1 | iex
#
# This installer is fully autonomous. It will:
#   1. Install Python 3.12 if not found (via winget or direct download)
#   2. Install git if not found (via winget or direct download)
#   3. Create ~/.kiro-gateway/ with a virtual environment
#   4. Install kiro-gateway from GitHub via pip
#   5. Create a .cmd wrapper in ~/.kiro-gateway/bin/
#   6. Add ~/.kiro-gateway/bin to your user PATH
#   7. Create a starter .env if none exists
#
# Idempotent: re-running this script upgrades safely without data loss.
# No pre-requisites needed - works on a fresh Windows install.

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/redok07/kiro-gateway.git"
$InstallDir = Join-Path $env:USERPROFILE ".kiro-gateway"
$BinDir = Join-Path $InstallDir "bin"
$VenvDir = Join-Path $InstallDir "venv"
$EnvFile = Join-Path $InstallDir ".env"
$LockFile = Join-Path $InstallDir ".install.lock"
$TempDir = Join-Path $env:TEMP "kiro-gateway-install"

# --- Helpers ---
function Write-Info  { param($Msg) Write-Host "  [info]  $Msg" -ForegroundColor Cyan }
function Write-Ok    { param($Msg) Write-Host "  [ok]    $Msg" -ForegroundColor Green }
function Write-Warn  { param($Msg) Write-Host "  [warn]  $Msg" -ForegroundColor Yellow }
function Write-Fail  { param($Msg) Write-Host "  [error] $Msg" -ForegroundColor Red; throw $Msg }

# --- Acquire install lock (prevent concurrent installs) ---
function Acquire-Lock {
    New-Item -ItemType Directory -Path $InstallDir -Force -ErrorAction SilentlyContinue | Out-Null
    if (Test-Path $LockFile) {
        $lockContent = Get-Content $LockFile -ErrorAction SilentlyContinue
        if ($lockContent) {
            $lockPid = [int]$lockContent
            try {
                $proc = Get-Process -Id $lockPid -ErrorAction Stop
                Write-Fail "Another installation is running (PID $lockPid). If stuck, delete $LockFile"
            } catch {
                Write-Warn "Stale lock file found, removing..."
            }
        }
        Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    }
    Set-Content -Path $LockFile -Value $PID -Encoding ASCII
}

# --- Release lock ---
function Release-Lock {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

# --- Network connectivity check ---
function Test-Network {
    Write-Info "Checking network connectivity..."
    $urls = @("https://github.com", "https://pypi.org")
    $connected = $false

    foreach ($url in $urls) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 -Method Head -ErrorAction Stop
            if ($response.StatusCode -lt 400) {
                $connected = $true
                break
            }
        } catch {
            continue
        }
    }

    if (-not $connected) {
        # Last resort: DNS check
        try {
            [System.Net.Dns]::GetHostEntry("github.com") | Out-Null
            $connected = $true
        } catch {}
    }

    if (-not $connected) {
        Write-Fail "No network connectivity. Cannot reach github.com or pypi.org.`nCheck your internet connection or proxy settings."
    }
    Write-Ok "Network OK"
}

# --- Find Python 3.10+ ---
function Find-Python {
    $candidates = @("python", "python3", "py")
    foreach ($cmd in $candidates) {
        try {
            $output = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) {
                $parts = $output.Trim().Split(".")
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
        $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            $output = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) {
                $parts = $output.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                    # Return full path to avoid "py -3" splitting issues
                    $pyPath = & py -3 -c "import sys; print(sys.executable)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $pyPath -and (Test-Path $pyPath.Trim())) {
                        return $pyPath.Trim()
                    }
                }
            }
        }
    } catch {}

    return $null
}

# --- Install Python ---
function Install-Python {
    Write-Info "Python 3.10+ not found. Installing Python 3.12..."

    # Try winget first (available on Windows 10 1709+ and Windows 11)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Using winget to install Python..."
        $wingetResult = & winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>&1
        if ($LASTEXITCODE -eq 0) {
            Refresh-SessionPath
            Write-Ok "Python installed via winget"
            return
        }
        Write-Warn "winget install failed, trying direct download..."
    }

    # Direct download from python.org
    Write-Info "Downloading Python 3.12 installer..."
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

    $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "win32" }
    $installerUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-$arch.exe"
    $installerPath = Join-Path $TempDir "python-installer.exe"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
    } catch {
        Write-Fail "Failed to download Python installer. Check your internet connection.`nURL: $installerUrl"
    }

    if (-not (Test-Path $installerPath) -or (Get-Item $installerPath).Length -lt 1MB) {
        Write-Fail "Python installer download appears corrupted (too small). Try again."
    }

    Write-Info "Running Python installer (silent, adds to PATH)..."
    $installArgs = "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1"
    $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -NoNewWindow -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Fail "Python installer exited with code $($proc.ExitCode). Try installing manually: https://www.python.org/downloads/"
    }

    Refresh-SessionPath

    # Clean up installer
    Remove-Item -Path $installerPath -Force -ErrorAction SilentlyContinue

    Write-Ok "Python 3.12 installed"
}

# --- Install git ---
function Install-Git {
    Write-Info "git not found. Installing git..."

    # Try winget first
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Using winget to install git..."
        $wingetResult = & winget install Git.Git --accept-package-agreements --accept-source-agreements --silent 2>&1
        if ($LASTEXITCODE -eq 0) {
            Refresh-SessionPath
            Write-Ok "git installed via winget"
            return
        }
        Write-Warn "winget install failed, trying direct download..."
    }

    # Direct download
    Write-Info "Downloading git installer..."
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

    $arch = if ([Environment]::Is64BitOperatingSystem) { "64-bit" } else { "32-bit" }
    $gitInstallerPath = Join-Path $TempDir "git-installer.exe"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -UseBasicParsing
        $asset = $releases.assets | Where-Object { $_.name -match "Git-.*-$arch\.exe$" -and $_.name -notmatch "portable" } | Select-Object -First 1
        if (-not $asset) {
            Write-Fail "Could not find git installer for $arch"
        }
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $gitInstallerPath -UseBasicParsing
    } catch {
        Write-Fail "Failed to download git installer. Check your internet connection."
    }

    if (-not (Test-Path $gitInstallerPath) -or (Get-Item $gitInstallerPath).Length -lt 1MB) {
        Write-Fail "Git installer download appears corrupted (too small). Try again."
    }

    Write-Info "Running git installer (silent)..."
    $proc = Start-Process -FilePath $gitInstallerPath -ArgumentList "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS" -Wait -NoNewWindow -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Fail "Git installer exited with code $($proc.ExitCode). Try installing manually: https://git-scm.com/downloads"
    }

    Refresh-SessionPath

    # Clean up
    Remove-Item -Path $gitInstallerPath -Force -ErrorAction SilentlyContinue

    Write-Ok "git installed"
}

# --- Refresh PATH for current session ---
function Refresh-SessionPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

# --- Add to user PATH (idempotent) ---
function Add-ToUserPath {
    param([string]$Dir)

    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")

    # Check if already present (case-insensitive, handles trailing slash)
    $normalizedDir = $Dir.TrimEnd('\', '/')
    if ($currentPath) {
        $entries = $currentPath.Split(';') | ForEach-Object { $_.TrimEnd('\', '/') }
        if ($entries -contains $normalizedDir) {
            Write-Info "$Dir already in PATH"
            return
        }
    }

    $newPath = if ($currentPath) { "$currentPath;$Dir" } else { $Dir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

    # Broadcast WM_SETTINGCHANGE so other processes pick up the change
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

# --- Validate existing venv is healthy ---
function Test-Venv {
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        return $false
    }

    # Check it actually runs and has pip
    try {
        & $venvPython -c "import pip; import sys; sys.exit(0)" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
    } catch {
        return $false
    }

    return $true
}

# --- Generate secure random key (guaranteed 32 chars) ---
function New-ApiKey {
    try {
        $bytes = New-Object byte[] 16
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $hex = [BitConverter]::ToString($bytes) -replace '-', ''
        return $hex.ToLower().Substring(0, 32)
    } catch {
        # Fallback: GUID-based (less random but functional)
        $raw = [Guid]::NewGuid().ToString("N") # 32 hex chars, no dashes
        return $raw.Substring(0, 32)
    }
}

# --- Main ---
function Main {
    Write-Host ""
    Write-Host "  Kiro Gateway Installer" -ForegroundColor White
    Write-Host "  Cross-platform proxy for Kiro API (Amazon Q Developer)" -ForegroundColor DarkGray
    Write-Host ""

    try {
        # Acquire lock (idempotent: stale locks are cleaned)
        Acquire-Lock

        # Network check before anything that needs internet
        Test-Network

        # 1. Ensure git is available
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Install-Git
            if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
                Write-Fail "Failed to install git. Install manually: https://git-scm.com/downloads"
            }
        } else {
            Write-Ok "git found: $(git --version)"
        }

        # 2. Ensure Python 3.10+ is available
        Write-Info "Looking for Python >= 3.10..."
        $python = Find-Python
        if (-not $python) {
            Install-Python
            $python = Find-Python
            if (-not $python) {
                Write-Fail "Failed to install Python 3.10+. Install manually: https://www.python.org/downloads/`nMake sure to check 'Add Python to PATH' during installation."
            }
        }

        $pyver = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
        Write-Ok "Found $python ($pyver)"

        # 3. Create install directory
        Write-Info "Setting up $InstallDir..."
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null

        # 4. Create or reuse venv (with health check)
        if (Test-Path $VenvDir) {
            if (Test-Venv) {
                Write-Info "Virtual environment exists and is healthy, reusing..."
            } else {
                Write-Warn "Virtual environment is corrupted, recreating..."
                Remove-Item -Path $VenvDir -Recurse -Force
                & $python -m venv $VenvDir
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "Failed to create virtual environment."
                }
            }
        } else {
            Write-Info "Creating virtual environment..."
            & $python -m venv $VenvDir
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "Failed to create virtual environment."
            }
        }
        Write-Ok "Virtual environment ready"

        # 5. Upgrade pip
        Write-Info "Ensuring pip is up to date..."
        $venvPython = Join-Path $VenvDir "Scripts\python.exe"
        & $venvPython -m pip install --upgrade --quiet pip 2>$null
        # Non-fatal if pip upgrade fails

        # 6. Install/upgrade kiro-gateway
        Write-Info "Installing kiro-gateway from GitHub (this may take a minute)..."
        $pip = Join-Path $VenvDir "Scripts\pip.exe"
        & $pip install --upgrade --quiet "git+$Repo"
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "pip install failed. Possible causes:`n  - No internet connection`n  - git not configured correctly`n  - GitHub rate limit (try again in a few minutes)"
        }

        # Verify installation actually worked
        $kiroExe = Join-Path $VenvDir "Scripts\kiro-gateway.exe"
        if (-not (Test-Path $kiroExe)) {
            Write-Fail "Installation appeared to succeed but kiro-gateway.exe not found.`nTry: Remove-Item -Recurse $VenvDir && re-run the installer"
        }

        $installedVer = "unknown"
        try {
            $installedVer = & $kiroExe --version 2>$null
        } catch {}
        Write-Ok "Installed kiro-gateway $installedVer"

        # 7. Create .cmd wrapper (idempotent: always overwrite)
        $wrapperPath = Join-Path $BinDir "kiro-gateway.cmd"
        $wrapperContent = @"
@echo off
REM Kiro Gateway launcher - auto-generated by installer
set "VENV_EXE=%~dp0..\venv\Scripts\kiro-gateway.exe"
if not exist "%VENV_EXE%" (
    echo [error] kiro-gateway.exe not found at %VENV_EXE% 1>&2
    echo         Re-run the installer to fix: irm https://raw.githubusercontent.com/redok07/kiro-gateway/main/install.ps1 ^| iex 1>&2
    exit /b 1
)
"%VENV_EXE%" %*
"@
        Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding ASCII

        # Also create a ps1 wrapper for PowerShell users
        $ps1Wrapper = Join-Path $BinDir "kiro-gateway.ps1"
        $ps1Content = @'
# Kiro Gateway launcher - auto-generated by installer
$exe = Join-Path $PSScriptRoot "..\venv\Scripts\kiro-gateway.exe"
if (-not (Test-Path $exe)) {
    Write-Error "kiro-gateway.exe not found at $exe. Re-run the installer."
    exit 1
}
& $exe @args
'@
        Set-Content -Path $ps1Wrapper -Value $ps1Content -Encoding UTF8

        # 8. Create .env if not exists (never overwrite user config)
        if (-not (Test-Path $EnvFile)) {
            $apiKey = New-ApiKey

            $envContent = @"
# Kiro Gateway Configuration
# Documentation: https://github.com/redok07/kiro-gateway#configuration
# Generated by installer - ready to use after adding your auth credential

# ============================================================
# PROXY PASSWORD (auto-generated, use this as your api_key)
# ============================================================
PROXY_API_KEY="$apiKey"

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
"@
            Set-Content -Path $EnvFile -Value $envContent -Encoding UTF8
            Write-Ok "Created $EnvFile"
            Write-Host "  Your auto-generated API key: " -NoNewline; Write-Host "$apiKey" -ForegroundColor White
            Write-Host "  Use this as api_key/password when connecting clients to the gateway." -ForegroundColor DarkGray
        } else {
            Write-Info ".env already exists, keeping current config"
        }

        # 9. Add to PATH (idempotent: checks before adding)
        Add-ToUserPath -Dir $BinDir

        # 10. Clean up temp
        if (Test-Path $TempDir) {
            Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        # 11. Done
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
    finally {
        Release-Lock
    }
}

Main
