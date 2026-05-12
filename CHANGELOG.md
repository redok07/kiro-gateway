# Changelog

All notable changes to Kiro Gateway are documented here.

## [2.4.10] - 2025-05-12

### Added

- **Interactive CLI** (`kiro-gateway` command)
  - Menu-driven interface: start/stop/status/configure/logs/accounts/uninstall
  - Background server management with PID file tracking
  - Account info display with token expiry status
  - Login wrapper (integrates with `tools/kiro_login.py`)
  - PATH registration (`setup-path` command)
  - Uninstall option (menu 8) with full cleanup

- **pip packaging** (`pyproject.toml`)
  - Install via: `pip install git+https://github.com/redok07/kiro-gateway.git`
  - Console entry point: `kiro-gateway`
  - Subcommands: `serve`, `start`, `stop`, `status`, `logs`, `setup-path`

- **Cross-platform one-line installers**
  - Linux/macOS: `curl -fsSL .../install.sh | bash`
  - Windows: `irm .../install.ps1 | iex`
  - Zero dependencies (auto-installs Python + git if missing)
  - Auto-generates random PROXY_API_KEY
  - Pre-fills server config (port 2507, region us-east-1)
  - Idempotent (safe to re-run for upgrades)
  - Lock file prevents concurrent installs
  - Network pre-check with DNS fallback
  - Venv health validation on re-install

- **Post-install usage guide** shown after installation
  - Auth method options explained
  - Client connection examples (OpenAI SDK, Anthropic SDK, curl)
  - Base URL and API key info

### Performance

- **Pure ASGI middleware** - replaced `BaseHTTPMiddleware` (eliminates response buffering that broke true streaming)
- **Dual HTTP/2 connection pools** - separate pools for streaming (50 conn) and non-streaming (100 conn), both with HTTP/2 multiplexing
- **Guarded json.dumps** - payload serialization skipped entirely when debug logging is off
- **asyncio.to_thread** for sync I/O - SQLite and file reads no longer block the event loop
- **list+join streaming** - replaced O(n^2) string concatenation with O(n) list accumulation
- **Pre-compiled regex** in model resolver (5 patterns compiled once at module load)
- **Cached machine fingerprint** - computed once at startup instead of per-request
- **Optimized payload trim loop** - pre-computed entry sizes instead of repeated json.dumps
- **Tiktoken preloaded at startup** - eliminates 100-200ms first-request latency
- **Lazy model_dump()** - Pydantic objects passed directly to tokenizer, serialized only when needed

### Fixed

- PID check uses exact CSV field matching (no substring false positives)
- Log reader caps memory at 64KB (seek-from-end instead of full file read)
- File handle closed immediately after subprocess inherits it
- Port 0 no longer treated as falsy in CLI args
- Password passed via env var instead of CLI argument (security)
- Credential path resolution works for pip-installed users
- `.env` resolution searches CWD > ~/.kiro-gateway/ > project root
- `install.ps1` skips Windows Store Python stubs
- `install.ps1` uses RNGCryptoServiceProvider (PS 5.1 compatible)
- `install.sh` DNS fallback handles macOS vs Linux ping differences

---

## [2.4.9] - 2025-05-10

### Added

- Kiro Auto-Login Tool with account management and browser automation
- Multi-account system with circuit breaker and sticky behavior

### Changed

- Updated .env.example with improved security guidance
- Enforced consistency and quality standards in documentation

---

## [2.4.8] - 2025-04-28

### Added

- Network error classification with user-friendly messages
- Per-request HTTP clients for streaming (fixes CLOSE_WAIT socket leaks)
- Cursor flat format support
- Inverted model names support
- HTTP/SOCKS5 proxy support (VPN_PROXY_URL)
- Enterprise Kiro IDE support
- AWS SSO OIDC authentication (Builder ID + corporate)

---

*For older releases, see [git log](https://github.com/redok07/kiro-gateway/commits/main).*
