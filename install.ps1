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
# Re-running this script upgrades an existing installation safely.
# No pre-requisites needed - works on a fresh Windows install.

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/redok07/kiro-gateway.git"
$InstallDir = Join-Path $env:USERPROFILE ".kiro-gateway"
$BinDir = Join-Path $InstallDir "bin"
$VenvDir = Join-Path $InstallDir "venv"
$EnvFile = Join-Path $InstallDir ".env"
$TempDir = Join-Path $env:TEMP "kiro-gateway-install"

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

function Install-Python {
    Write-Info "Python 3.10+ not found. Installing Python 3.12..."

    # Try winget first (available on Windows 10 1709+ and Windows 11)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Using winget to install Python..."
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>$null
        if ($LASTEXITCODE -eq 0) {
            # Refresh PATH for current session
            $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $env:Path = "$machinePath;$userPath"
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

    Write-Info "Running Python installer (silent, adds to PATH)..."
    $installArgs = "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1"
    Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -NoNewWindow

    # Refresh PATH
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"

    # Clean up installer
    Remove-Item -Path $installerPath -Force -ErrorAction SilentlyContinue

    Write-Ok "Python 3.12 installed"
}

function Install-Git {
    Write-Info "git not found. Installing git..."

    # Try winget first
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Using winget to install git..."
        winget install Git.Git --accept-package-agreements --accept-source-agreements --silent 2>$null
        if ($LASTEXITCODE -eq 0) {
            $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $env:Path = "$machinePath;$userPath"
            Write-Ok "git installed via winget"
            return
        }
        Write-Warn "winget install failed, trying direct download..."
    }

    # Direct download
    Write-Info "Downloading git installer..."
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

    $arch = if ([Environment]::Is64BitOperatingSystem) { "64-bit" } else { "32-bit" }
    # Use Git for Windows release API to get latest
    $gitInstallerPath = Join-Path $TempDir "git-installer.exe"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        # Get latest release info
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -UseBasicParsing
        $asset = $releases.assets | Where-Object { $_.name -match "Git-.*-$arch\.exe$" -and $_.name -notmatch "portable" } | Select-Object -First 1
        if (-not $asset) {
            Write-Fail "Could not find git installer for $arch"
        }
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $gitInstallerPath -UseBasicParsing
    } catch {
        Write-Fail "Failed to download git installer. Check your internet connection."
    }

    Write-Info "Running git installer (silent)..."
    Start-Process -FilePath $gitInstallerPath -ArgumentList "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/COMPONENTS=`"icons,ext\reg\shellhere,assoc,assoc_sh`"" -Wait -NoNewWindow

    # Refresh PATH
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"

    # Clean up
    Remove-Item -Path $gitInstallerPath -Force -ErrorAction SilentlyContinue

    Write-Ok "git installed"
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

    # 4. Create or reuse venv
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

    # 5. Upgrade pip
    Write-Info "Ensuring pip is up to date..."
    & (Join-Path $VenvDir "Scripts\python.exe") -m pip install --upgrade --quiet pip 2>$null

    # 6. Install/upgrade kiro-gateway
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

    # 7. Create .cmd wrapper
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

    # 8. Create .env if not exists
    if (-not (Test-Path $EnvFile)) {
        # Auto-generate a secure random PROXY_API_KEY
        $bytes = New-Object byte[] 24
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $apiKey = [Convert]::ToBase64String($bytes) -replace '[/+=]','' | ForEach-Object { $_.Substring(0, [Math]::Min(32, $_.Length)) }

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

    # 9. Add to PATH
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

Main
