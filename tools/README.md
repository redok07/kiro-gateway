# Kiro Auto-Login Tool

Automates Kiro OAuth PKCE login via Google using [Camoufox](https://github.com/nichochar/camoufox) (anti-detect browser). Saves credentials in Kiro IDE-compatible JSON format, ready for kiro-gateway.

## Features

- **Single-account or batch multi-account login**
- **Kiro IDE-compatible output** — drop into `~/.aws/sso/cache/`
- **Auto-register** into kiro-gateway's `credentials.json` (`--register`)
- **Retry with exponential backoff** + automatic fallback to visible browser on block
- **Token validation** against AWS Q API before saving (fail fast)
- **Atomic writes** — no partial corruption if interrupted
- **Secure password input** — env var or interactive prompt, never CLI args
- **Proxy support** — HTTP/SOCKS5 per-account or global

## Quick Start

```powershell
# 1. Create virtual environment
cd tools
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Login (prompts for password interactively)
python kiro_login.py login --email your@gmail.com --register
```

## Usage

### Single Account Login

```powershell
# Interactive password prompt (safest)
python kiro_login.py login --email user@gmail.com

# Password from environment variable
$env:KIRO_LOGIN_PASSWORD = "your-password"
python kiro_login.py login --email user@gmail.com

# Auto-register into kiro-gateway credentials.json
python kiro_login.py login --email user@gmail.com --register

# Non-headless mode (to solve captcha/2FA manually)
python kiro_login.py login --email user@gmail.com --no-headless

# With proxy
python kiro_login.py login --email user@gmail.com --proxy http://127.0.0.1:7890
```

### Batch Login

```powershell
# Create accounts.json from example
copy accounts.json.example accounts.json
# Edit accounts.json with your accounts...

# Run batch login
python kiro_login.py batch --config accounts.json --register
```

### Validate Existing Credentials

```powershell
python kiro_login.py validate --credentials-file ~/.aws/sso/cache/kiro-auto-user.json
```

## Options

| Flag | Description |
|------|-------------|
| `--email` | Google account email (required for `login`) |
| `--password` | Password directly (discouraged, visible in history) |
| `--password-env` | Name of env var containing password |
| `--alias` | Short label for the account (default: derived from email) |
| `--output` | Custom output path for credentials file |
| `--no-headless` | Show browser window (for captcha/2FA) |
| `--proxy` | HTTP/SOCKS5 proxy URL |
| `--retries` | Number of retry attempts (default: 3) |
| `--retry-delay` | Base delay between retries in seconds (default: 3.0) |
| `--register` | Auto-register in kiro-gateway's `credentials.json` |
| `--credentials-json` | Path to kiro-gateway credentials.json (default: `./credentials.json`) |
| `--no-validate` | Skip token validation against AWS Q API |

## Batch Config Format (`accounts.json`)

```json
[
  {
    "email": "user@gmail.com",
    "password_env": "KIRO_LOGIN_PASSWORD_1",
    "alias": "main",
    "enabled": true
  }
]
```

Fields:
- `email` (required): Google account email
- `password` or `password_env` (required): Password directly or env var name
- `alias` (optional): Short label, defaults to sanitized email
- `output` (optional): Custom output file path
- `proxy` (optional): Per-account proxy URL
- `enabled` (optional): Set to `false` to skip (default: `true`)

## Output

Credentials are saved in Kiro IDE-compatible format:

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "expiresAt": "2025-01-15T12:00:00.000Z",
  "region": "us-east-1",
  "profileArn": "arn:aws:codewhisperer:us-east-1:..."
}
```

Default output location: `~/.aws/sso/cache/kiro-auto-<alias>.json`

## Security Warnings

⚠️ **This script automates Google login.** Google may flag your account as bot activity or temporarily suspend it. Use only for YOUR OWN accounts.

Best practices:
- **Never** store passwords in cleartext files — use env vars or interactive prompts
- Keep `accounts.json` ACL-restricted and gitignored
- Throttle logins (don't run in a tight loop)
- If captcha appears, run with `--no-headless` and solve manually
- Use app-specific passwords if your account has 2FA

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Captcha/block on headless | Run with `--no-headless` to solve manually |
| "Missing dependency" error | Run `pip install -r requirements.txt` in venv |
| Token validation fails | Account may have no Q Developer access |
| Stuck on email step | Google may require phone verification |
| Proxy connection error | Check proxy URL format and availability |

## How It Works

1. Generates PKCE code verifier + challenge
2. Opens Kiro auth URL (which redirects to Google OAuth)
3. Automates Google email/password form filling via Camoufox
4. Intercepts the `kiro://` redirect containing the auth code
5. Exchanges auth code for access/refresh tokens via Kiro token endpoint
6. Validates token against AWS Q `/getUsageLimits` API
7. Saves credentials in Kiro IDE-compatible JSON format
8. Optionally registers in kiro-gateway's `credentials.json`
