#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kiro Auto-Login Tool for kiro-gateway
======================================

Automates Kiro OAuth PKCE login via Google using Camoufox (anti-detect browser).
Saves credentials in Kiro IDE-compatible JSON format, ready for kiro-gateway.

Features
--------
- Single-account or batch multi-account login
- Output as Kiro IDE-compatible credentials JSON (drop into ~/.aws/sso/cache/)
- Auto-register accounts in kiro-gateway's credentials.json (--register)
- Retry with exponential backoff + automatic fallback to visible browser on block
- Token validation against AWS Q API before saving (fail fast)
- Atomic writes (no partial corruption if interrupted)
- Secure password input (env var or interactive prompt, never CLI args)
- Windows-friendly path handling

Usage
-----
    # Single login (prompts for password)
    python kiro_login.py login --email user@gmail.com

    # Password from env var (safer for automation)
    $env:KIRO_LOGIN_PASSWORD = "secret"
    python kiro_login.py login --email user@gmail.com

    # Auto-register into kiro-gateway credentials.json
    python kiro_login.py login --email user@gmail.com --register

    # Non-headless (to solve captcha/2FA manually)
    python kiro_login.py login --email user@gmail.com --no-headless

    # Batch login from config
    python kiro_login.py batch --config accounts.json --register

Security
--------
WARNING: This script automates Google login. Google may flag your account as
bot activity or temporarily suspend it. Use only for YOUR OWN accounts.

Best practices:
  - Do not store passwords in cleartext files. Use env vars or prompts.
  - Keep accounts.json ACL-restricted and gitignored.
  - Throttle logins (don't run in a tight loop).
  - If captcha appears, run with --no-headless and solve manually.

Install
-------
    cd tools
    python -m venv venv
    .\\venv\\Scripts\\Activate.ps1          # Windows PowerShell
    pip install -r requirements.txt
    # (Camoufox downloads a ~100MB Firefox fork on first run)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse


# ==================================================================================================
# Constants
# ==================================================================================================

KIRO_AUTH_BASE = "https://prod.us-east-1.auth.desktop.kiro.dev"
KIRO_LOGIN_ENDPOINT = f"{KIRO_AUTH_BASE}/login"
KIRO_TOKEN_ENDPOINT = f"{KIRO_AUTH_BASE}/oauth/token"
KIRO_REDIRECT_URI = "kiro://kiro.kiroAgent/authenticate-success"
KIRO_USAGE_ENDPOINT = "https://q.us-east-1.amazonaws.com/getUsageLimits"
KIRO_USER_AGENT = "aws-sdk-js/1.0.36 ua/2.1 os/darwin#24.6.0 lang/js md/nodejs#22.22.0 api/codewhispererstreaming#1.0.36 m/E KiroIDE-0.12.200"
KIRO_X_AMZ_USER_AGENT = "aws-sdk-js/1.0.36 KiroIDE-0.12.200"

DEFAULT_REGION = "us-east-1"
DEFAULT_OUTPUT_DIR = Path.home() / ".aws" / "sso" / "cache"

AUTH_LOOP_MAX_ITERATIONS = 180        # ~3 minutes at 1s/iteration
GOOGLE_EMAIL_STUCK_TIMEOUT = 60.0     # abort if stuck on email step this long
CHALLENGE_WAIT_TIMEOUT = 180.0        # 3 min to manually solve captcha in --no-headless
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_BASE_DELAY = 3.0        # seconds; exponential: 3, 6, 12, ...

# SSL context that tolerates proxy MITM / local dev
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ==================================================================================================
# Utilities
# ==================================================================================================

def log(level: str, msg: str) -> None:
    """Structured progress log to stderr (keeps stdout clean for machine-readable result)."""
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "info":  "\033[36m[INFO]\033[0m ",
        "ok":    "\033[32m[ OK ]\033[0m ",
        "warn":  "\033[33m[WARN]\033[0m ",
        "err":   "\033[31m[ERR ]\033[0m ",
        "step":  "\033[35m[STEP]\033[0m ",
    }.get(level, "[....] ")
    print(f"{ts} {prefix}{msg}", file=sys.stderr, flush=True)


def emit_result(payload: dict) -> None:
    """Machine-readable result on stdout (JSON)."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def expand_path(p: str | os.PathLike) -> Path:
    """Expand ~ and env vars; normalize for OS."""
    return Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: write to temp file, then rename."""
    path = expand_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sanitize_alias(s: str) -> str:
    """Make a filesystem-safe alias from arbitrary input (e.g., email)."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_\-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "account"


# ==================================================================================================
# PKCE
# ==================================================================================================

def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def extract_code_from_kiro_url(url: str) -> Optional[str]:
    """Extract auth code from kiro:// redirect URL."""
    if not url or not url.startswith("kiro://"):
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    values = params.get("code")
    return values[0] if values else None


# ==================================================================================================
# HTTP - token exchange and usage fetch
# ==================================================================================================

async def exchange_code_for_tokens(code: str, verifier: str) -> dict:
    """Exchange auth code for accessToken/refreshToken/profileArn."""
    import aiohttp
    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                KIRO_TOKEN_ENDPOINT,
                json={
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": KIRO_REDIRECT_URI,
                },
                headers={"Content-Type": "application/json"},
                ssl=_SSL_CTX,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    return {"error": f"Token exchange HTTP {resp.status}: {body[:300]}"}
                payload = json.loads(body)
                if not payload.get("accessToken"):
                    return {"error": "Token response missing accessToken"}
                return {
                    "access_token": payload["accessToken"],
                    "refresh_token": payload.get("refreshToken", ""),
                    "profile_arn": str(payload.get("profileArn") or "").strip(),
                    "expires_in": int(payload.get("expiresIn") or 0),
                }
    except Exception as exc:
        return {"error": f"Token exchange exception: {exc}"}


async def fetch_kiro_usage(access_token: str, profile_arn: str = "") -> Optional[dict]:
    """Validate the access token by fetching usage/quota from AWS Q API."""
    import aiohttp
    params = ["origin=AI_EDITOR", "resourceType=AGENTIC_REQUEST"]
    if profile_arn:
        params.append(f"profileArn={quote(profile_arn, safe='')}")
    url = KIRO_USAGE_ENDPOINT + "?" + "&".join(params)
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": KIRO_USER_AGENT,
                    "x-amz-user-agent": KIRO_X_AMZ_USER_AGENT,
                },
                ssl=_SSL_CTX,
            ) as resp:
                if resp.status != 200:
                    return {"_http_status": resp.status, "_ok": False}
                payload = await resp.json()
                return _parse_usage(payload)
    except Exception as exc:
        return {"_ok": False, "_error": str(exc)}


def _parse_usage(payload: dict) -> dict:
    usage_list = payload.get("usageBreakdownList") or []
    if not usage_list:
        return {"_ok": True, "totalCredits": 0, "remainingCredits": 0,
                "usedCredits": 0, "packageName": "Free", "expiresAt": ""}
    usage = usage_list[0] or {}
    total = float(usage.get("usageLimit") or usage.get("usageLimitWithPrecision") or 0)
    used  = float(usage.get("currentUsage") or usage.get("currentUsageWithPrecision") or 0)

    ft = usage.get("freeTrialInfo") or {}
    if str(ft.get("freeTrialStatus") or "").upper() == "ACTIVE":
        total += float(ft.get("usageLimit") or ft.get("usageLimitWithPrecision") or 0)
        used  += float(ft.get("currentUsage") or ft.get("currentUsageWithPrecision") or 0)

    for bonus in usage.get("bonuses") or []:
        total += float((bonus or {}).get("usageLimit") or 0)
        used  += float((bonus or {}).get("currentUsage") or 0)

    remaining = max(total - used, 0)
    sub_title = str(
        payload.get("subscriptionInfo", {}).get("subscriptionTitle")
        or payload.get("subscriptionTitle")
        or payload.get("subscriptionType")
        or "Free"
    ).strip()
    next_reset = payload.get("nextDateReset") or payload.get("nextResetDate") or ""

    return {
        "_ok": True,
        "totalCredits": total,
        "remainingCredits": remaining,
        "usedCredits": used,
        "packageName": sub_title,
        "expiresAt": next_reset,
    }


# ==================================================================================================
# Browser automation - Google OAuth form filling via Camoufox
#
# The low-level form-filling helpers are adapted from the public hexos/kadangkesel
# kiro_login.py, which uses a proven wait_for_selector + Locator API approach
# (not page.evaluate() which hangs in Camoufox). Refactored here for structured
# errors and clearer state tracking.
# ==================================================================================================

async def _is_email_step(page) -> bool:
    for sel in ("#identifierId", 'input[type="email"]', 'input[name="identifier"]'):
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            pass
    return False


async def _is_password_step(page) -> bool:
    for sel in ('input[name="Passwd"]', 'input[type="password"]'):
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            pass
    return False


async def _click_next(page) -> bool:
    for sel in (
        "#identifierNext button",
        "#passwordNext button",
        "#identifierNext",
        "#passwordNext",
    ):
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(force=True)
                return True
        except Exception:
            continue
    return False


async def _fill_google_email(page, email: str) -> bool:
    selectors = (
        "#identifierId",
        'input[type="email"]',
        'input[name="identifier"]',
        'input[autocomplete="username"]',
    )
    found = None
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=3000)
            found = sel
            break
        except Exception:
            continue
    if not found:
        return False

    try:
        loc = page.locator(found).first
        if await loc.count() == 0 or not await loc.is_visible():
            return False
        await loc.scroll_into_view_if_needed()
        await loc.click(force=True)
        await asyncio.sleep(0.2)
        try:
            await loc.press("Control+a")
            await loc.press("Backspace")
        except Exception:
            pass
        await loc.press_sequentially(email, delay=60)
        await asyncio.sleep(0.4)
        val = (await loc.input_value()) or ""
        if email.lower() != val.lower().strip():
            log("warn", f"Email mismatch: expected {email!r}, got {val!r}")
            return False
        await asyncio.sleep(0.2)
        if not await _click_next(page):
            await loc.press("Enter")
        try:
            await page.wait_for_selector(
                'input[name="Passwd"], input[type="password"]',
                state="visible", timeout=10000,
            )
        except Exception:
            await asyncio.sleep(2.0)
        return True
    except Exception as exc:
        log("warn", f"Email fill error: {exc}")
        return False


async def _fill_google_password(page, password: str) -> bool:
    found = None
    for sel in ('input[name="Passwd"]', 'input[type="password"]'):
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            found = sel
            break
        except Exception:
            continue
    if not found:
        return False

    try:
        loc = page.locator(found).first
        if await loc.count() == 0 or not await loc.is_visible():
            return False
        await loc.scroll_into_view_if_needed()
        await loc.click(force=True)
        await asyncio.sleep(0.2)
        try:
            await loc.press("Control+a")
            await loc.press("Backspace")
        except Exception:
            pass
        await loc.press_sequentially(password, delay=70)
        await asyncio.sleep(0.4)
        if not await _click_next(page):
            await loc.press("Enter")
        try:
            await page.wait_for_function(
                """() => {
                    const host = window.location.host || '';
                    const path = window.location.pathname || '';
                    const hasPwd = Array.from(
                        document.querySelectorAll('input[name="Passwd"], input[type="password"]')
                    ).some(el => el.offsetParent !== null);
                    if (!host.includes('accounts.google.com')) return true;
                    if (!path.includes('/challenge/pwd')) return true;
                    return !hasPwd;
                }""",
                timeout=12000,
            )
        except Exception:
            await asyncio.sleep(3.0)
        return True
    except Exception as exc:
        log("warn", f"Password fill error: {exc}")
        return False


async def _handle_consent(page) -> bool:
    """Handle consent-like 'Continue' pages (not on password/identifier steps)."""
    try:
        if "accounts.google.com" not in (page.url or ""):
            return False
        parsed = urlparse(page.url)
        path = parsed.path.lower()
        if "/signin/identifier" in path or "/challenge/pwd" in path:
            return False
        for text in ("Continue", "Allow", "Lanjutkan", "I agree"):
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.8)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _handle_gaplustos(page) -> bool:
    """Handle Google G+ ToS acknowledgment page."""
    try:
        if "/speedbump/gaplustos" not in (page.url or ""):
            return False
        for sel in ("#confirm", 'input[name="confirm"]', 'input[type="submit"]'):
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(force=True)
                    return True
            except Exception:
                continue
        for text in ("I understand", "Saya mengerti"):
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _detect_blocking(page) -> Optional[str]:
    """Return a classification string if Google is blocking, else None."""
    try:
        url = page.url or ""
        if "accounts.google.com" not in url:
            return None
        parsed = urlparse(url)
        path = parsed.path

        if "/challenge/" in path:
            for np in ("/challenge/pwd", "/challenge/selection", "/challenge/ipp"):
                if np in path:
                    return None
            return f"soft:google challenge ({path})"

        markers = (
            ("captcha",                              "hard:captcha"),
            ("try again later",                      "hard:rate_limited"),
            ("this browser or app may not be secure","hard:browser_blocked"),
            ("unusual traffic",                      "hard:unusual_traffic"),
        )
        for keyword, tag in markers:
            try:
                loc = page.get_by_text(keyword, exact=False).first
                if await loc.count() > 0 and await loc.is_visible():
                    return tag
            except Exception:
                continue
    except Exception:
        pass
    return None


# ==================================================================================================
# Core: single login attempt
# ==================================================================================================

@dataclass
class LoginAttemptResult:
    success: bool
    access_token: str = ""
    refresh_token: str = ""
    profile_arn: str = ""
    expires_in: int = 0
    error: str = ""
    blocked: bool = False       # True if Google blocked us (hint: try non-headless)


async def _run_single_login(
    email: str,
    password: str,
    *,
    headless: bool = True,
    proxy_url: str = "",
) -> LoginAttemptResult:
    """Single attempt at Kiro PKCE login. Returns LoginAttemptResult."""
    try:
        from browserforge.fingerprints import Screen
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        return LoginAttemptResult(
            success=False,
            error=f"Missing dependency: {exc}. Run: pip install -r requirements.txt",
        )

    verifier, challenge = generate_pkce_pair()
    state = str(uuid.uuid4())
    auth_url = f"{KIRO_LOGIN_ENDPOINT}?" + urlencode({
        "idp": "Google",
        "redirect_uri": KIRO_REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "select_account",
    })

    camoufox_kwargs: dict = {
        "headless": headless,
        "os": "windows",
        "block_webrtc": True,
        "humanize": False,
        "screen": Screen(max_width=1920, max_height=1080),
        "i_know_what_im_doing": True,
    }
    if proxy_url:
        parsed = urlparse(proxy_url)
        proxy_cfg: dict = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy_cfg["username"] = parsed.username
        if parsed.password:
            proxy_cfg["password"] = parsed.password
        camoufox_kwargs["proxy"] = proxy_cfg
        camoufox_kwargs["geoip"] = True
        log("info", f"Proxy: {proxy_url}")

    log("step", f"Launching Camoufox (headless={headless})...")
    manager = AsyncCamoufox(**camoufox_kwargs)

    auth_code: Optional[str] = None

    try:
        browser = await manager.__aenter__()
        page = await browser.new_page()
        page.set_default_timeout(20000)

        # Intercept kiro:// from response Location headers
        def on_response(response):
            nonlocal auth_code
            if auth_code:
                return
            try:
                loc = response.headers.get("location", "")
                c = extract_code_from_kiro_url(loc)
                if c:
                    auth_code = c
            except Exception:
                pass
        page.on("response", on_response)

        log("step", "Navigating to Kiro auth URL...")
        try:
            await page.goto(auth_url, wait_until="commit", timeout=90000)
        except Exception as nav_exc:
            log("warn", f"First navigation failed: {nav_exc}, retrying once...")
            await asyncio.sleep(3.0)
            try:
                await page.goto(auth_url, wait_until="commit", timeout=90000)
            except Exception as nav_exc2:
                return LoginAttemptResult(success=False, error=f"Navigation failed: {nav_exc2}")
        await asyncio.sleep(2.0)

        # Also intercept kiro:// from subsequent navigations
        async def route_handler(route):
            nonlocal auth_code
            if auth_code:
                try:
                    await route.continue_()
                except Exception:
                    pass
                return
            req_url = route.request.url
            c = extract_code_from_kiro_url(req_url)
            if c:
                auth_code = c
                try:
                    await route.abort()
                except Exception:
                    pass
                return
            try:
                await route.continue_()
            except Exception:
                pass
        await page.route("**/*", route_handler)

        # Auth loop
        log("step", "Automating Google login...")
        page.set_default_timeout(5000)
        email_deadline = 0.0
        password_deadline = 0.0
        email_step_started: Optional[float] = None

        for iteration in range(AUTH_LOOP_MAX_ITERATIONS):
            if auth_code:
                log("ok", "Authorization code captured.")
                break

            try:
                cur = page.url or ""
            except Exception:
                cur = ""

            c_url = extract_code_from_kiro_url(cur)
            if c_url:
                auth_code = c_url
                log("ok", "Authorization code captured (via URL).")
                break

            parsed = urlparse(cur) if cur else None
            on_google = bool(parsed and "accounts.google.com" in (parsed.netloc or ""))
            now = time.monotonic()

            if "SetSID" in cur or "/accounts/set" in cur.lower():
                await asyncio.sleep(0.5)
                continue

            if on_google:
                at_pwd   = await _is_password_step(page)
                at_email = await _is_email_step(page)

                if at_email and not at_pwd:
                    if email_step_started is None:
                        email_step_started = now
                    elif now - email_step_started > GOOGLE_EMAIL_STUCK_TIMEOUT:
                        return LoginAttemptResult(
                            success=False,
                            error="Stuck on Google email step >60s (captcha likely)",
                            blocked=True,
                        )
                    if now < email_deadline:
                        await asyncio.sleep(0.4)
                        continue
                    log("step", "Filling Google email...")
                    if await _fill_google_email(page, email):
                        email_deadline = time.monotonic() + 6.0
                        await asyncio.sleep(1.0)
                        continue

                if at_pwd:
                    email_step_started = None
                    if now < password_deadline:
                        await asyncio.sleep(0.4)
                        continue
                    log("step", "Filling Google password...")
                    if await _fill_google_password(page, password):
                        password_deadline = time.monotonic() + 8.0
                        await asyncio.sleep(1.0)
                        continue

                if at_email or at_pwd:
                    await asyncio.sleep(0.6)
                    continue

                if await _handle_gaplustos(page):
                    await asyncio.sleep(0.8)
                    continue
                if await _handle_consent(page):
                    await asyncio.sleep(0.8)
                    continue

                tag = await _detect_blocking(page)
                if tag:
                    if tag.startswith("hard:"):
                        return LoginAttemptResult(
                            success=False,
                            error=f"Google blocked: {tag[5:]}",
                            blocked=True,
                        )
                    # Soft challenge (2FA, verification, etc.)
                    if not headless:
                        log("warn",
                            f"Challenge detected ({tag[5:]}); waiting "
                            f"{int(CHALLENGE_WAIT_TIMEOUT)}s for manual resolution...")
                        deadline = time.monotonic() + CHALLENGE_WAIT_TIMEOUT
                        resolved = False
                        while time.monotonic() < deadline:
                            await asyncio.sleep(0.5)
                            if auth_code:
                                resolved = True
                                break
                            try:
                                new_url = page.url or ""
                            except Exception:
                                new_url = ""
                            if "accounts.google.com" not in new_url or "/challenge/" not in new_url:
                                resolved = True
                                log("ok", "Challenge resolved.")
                                break
                        if not resolved:
                            return LoginAttemptResult(
                                success=False,
                                error=f"Challenge timeout: {tag[5:]}",
                                blocked=True,
                            )
                        continue
                    else:
                        return LoginAttemptResult(
                            success=False,
                            error=f"Verification required: {tag[5:]}",
                            blocked=True,
                        )

            # Fallthrough nudge
            await asyncio.sleep(1.0)
        else:
            return LoginAttemptResult(
                success=False,
                error="Auth loop timeout: no authorization code captured",
            )

        if not auth_code:
            return LoginAttemptResult(success=False, error="No authorization code captured")

        log("step", "Exchanging code for tokens...")
        tr = await exchange_code_for_tokens(auth_code, verifier)
        if "error" in tr:
            return LoginAttemptResult(success=False, error=tr["error"])

        return LoginAttemptResult(
            success=True,
            access_token=tr["access_token"],
            refresh_token=tr.get("refresh_token", ""),
            profile_arn=tr.get("profile_arn", ""),
            expires_in=int(tr.get("expires_in") or 0),
        )

    except Exception as exc:
        return LoginAttemptResult(success=False, error=f"Unexpected error: {exc}")
    finally:
        try:
            await manager.__aexit__(None, None, None)
        except Exception:
            pass


# ==================================================================================================
# Orchestration: retry + fallback + validate + save
# ==================================================================================================

@dataclass
class AccountSpec:
    email: str
    password: str
    alias: str = ""          # optional label; default derived from email
    output: str = ""         # optional output path override
    proxy: str = ""          # optional per-account proxy
    enabled: bool = True

    def effective_alias(self) -> str:
        return sanitize_alias(self.alias) if self.alias else sanitize_alias(self.email)

    def effective_output(self) -> Path:
        if self.output:
            return expand_path(self.output)
        return DEFAULT_OUTPUT_DIR / f"kiro-auto-{self.effective_alias()}.json"


def build_kiro_credentials_json(
    access_token: str,
    refresh_token: str,
    profile_arn: str = "",
    region: str = DEFAULT_REGION,
    expires_in: int = 0,
) -> dict:
    """Build a Kiro IDE-compatible credentials JSON object."""
    if expires_in > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    else:
        # Fallback: 1 hour. kiro-gateway refreshes early based on TOKEN_REFRESH_THRESHOLD.
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    expires_at_str = expires_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    out = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_str,
        "region": region,
    }
    if profile_arn:
        out["profileArn"] = profile_arn
    return out


def register_in_credentials_json(
    credentials_json_path: Path,
    account_file: Path,
    alias: str,
) -> bool:
    """Add an entry to kiro-gateway's credentials.json (idempotent)."""
    credentials_json_path = expand_path(credentials_json_path)
    account_file_str = str(account_file)

    accounts: list[dict] = []
    if credentials_json_path.exists():
        try:
            accounts = json.loads(credentials_json_path.read_text(encoding="utf-8"))
            if not isinstance(accounts, list):
                log("warn", "credentials.json is not a list, overwriting.")
                accounts = []
        except Exception as exc:
            log("warn", f"Could not parse existing credentials.json ({exc}), overwriting.")
            accounts = []

    for entry in accounts:
        if isinstance(entry, dict):
            ep = entry.get("path") or ""
            if ep and expand_path(ep) == account_file:
                log("info", f"Already registered in credentials.json: {alias}")
                return False

    accounts.append({
        "type": "json",
        "path": account_file_str,
        "comment": f"Added by kiro_login.py (alias={alias})",
    })
    atomic_write_json(credentials_json_path, accounts)
    log("ok", f"Registered account in {credentials_json_path.name}")
    return True


async def run_login_with_retry(
    spec: AccountSpec,
    *,
    headless: bool,
    proxy_url: str,
    retry_count: int,
    base_delay: float,
) -> LoginAttemptResult:
    """Run login with retries. Auto-flips to visible browser on block."""
    last = LoginAttemptResult(success=False, error="no attempt made")
    headless_now = headless
    for attempt in range(1, retry_count + 1):
        log("info", f"Attempt {attempt}/{retry_count} for {spec.email} (headless={headless_now})")
        last = await _run_single_login(
            email=spec.email,
            password=spec.password,
            headless=headless_now,
            proxy_url=proxy_url,
        )
        if last.success:
            return last
        log("warn", f"Attempt {attempt} failed: {last.error}")

        if last.blocked and headless_now:
            log("warn", "Google blocked; switching to visible browser for next attempt.")
            headless_now = False

        if attempt < retry_count:
            delay = base_delay * (2 ** (attempt - 1))
            log("info", f"Waiting {delay:.1f}s before retry...")
            await asyncio.sleep(delay)
    return last


async def login_and_save(
    spec: AccountSpec,
    *,
    headless: bool,
    proxy_url: str,
    retry_count: int,
    base_delay: float,
    register: bool,
    register_path: Path,
    validate: bool,
) -> dict:
    """High-level: login one account, validate, save, optionally register."""
    attempt = await run_login_with_retry(
        spec, headless=headless, proxy_url=proxy_url,
        retry_count=retry_count, base_delay=base_delay,
    )
    if not attempt.success:
        return {
            "email": spec.email,
            "alias": spec.effective_alias(),
            "success": False,
            "error": attempt.error,
        }

    credit: Optional[dict] = None
    if validate:
        log("step", "Validating token against AWS Q /getUsageLimits...")
        credit = await fetch_kiro_usage(attempt.access_token, attempt.profile_arn)
        if not credit or not credit.get("_ok"):
            return {
                "email": spec.email,
                "alias": spec.effective_alias(),
                "success": False,
                "error": f"Token validation failed: {credit}",
            }
        log("ok",
            f"Token valid. Remaining: {credit.get('remainingCredits', 0):.0f}/"
            f"{credit.get('totalCredits', 0):.0f} ({credit.get('packageName','')})")

    out_path = spec.effective_output()
    payload = build_kiro_credentials_json(
        access_token=attempt.access_token,
        refresh_token=attempt.refresh_token,
        profile_arn=attempt.profile_arn,
        region=DEFAULT_REGION,
        expires_in=attempt.expires_in,
    )
    atomic_write_json(out_path, payload)
    log("ok", f"Saved credentials to {out_path}")

    registered = False
    if register:
        registered = register_in_credentials_json(register_path, out_path, spec.effective_alias())

    return {
        "email": spec.email,
        "alias": spec.effective_alias(),
        "success": True,
        "output": str(out_path),
        "registered": registered,
        "profileArn": attempt.profile_arn,
        "credit": {k: v for k, v in (credit or {}).items() if not k.startswith("_")},
    }


# ==================================================================================================
# Config loading for batch mode
# ==================================================================================================

def load_accounts_from_config(path: Path) -> list[AccountSpec]:
    data = json.loads(expand_path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("accounts config must be a JSON list of objects")
    out: list[AccountSpec] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"accounts[{i}] must be an object")
        email = (entry.get("email") or "").strip()
        if not email:
            raise ValueError(f"accounts[{i}] missing 'email'")

        password = entry.get("password") or ""
        if not password:
            env_name = entry.get("password_env") or ""
            if env_name:
                password = os.getenv(env_name, "")
            if not password:
                raise ValueError(
                    f"accounts[{i}] ({email}): missing password/password_env. "
                    f"Set 'password' or 'password_env' pointing to an env var."
                )
        out.append(AccountSpec(
            email=email,
            password=password,
            alias=entry.get("alias", "") or "",
            output=entry.get("output", "") or "",
            proxy=entry.get("proxy", "") or "",
            enabled=bool(entry.get("enabled", True)),
        ))
    return out


# ==================================================================================================
# CLI
# ==================================================================================================

def _obtain_password(email: str, provided: Optional[str], from_env: Optional[str]) -> str:
    """Resolve password: --password (discouraged) > env var > interactive prompt."""
    if provided:
        log("warn", "Password passed as CLI arg - visible in shell history. "
                    "Prefer $KIRO_LOGIN_PASSWORD or interactive prompt.")
        return provided
    if from_env:
        val = os.getenv(from_env, "")
        if val:
            return val
        log("warn", f"Env var {from_env!r} not set or empty.")
    val = os.getenv("KIRO_LOGIN_PASSWORD", "")
    if val:
        return val
    try:
        return getpass.getpass(f"Google password for {email}: ")
    except Exception as exc:
        log("err", f"Cannot read password from terminal: {exc}")
        return ""


async def _cmd_login(args: argparse.Namespace) -> int:
    password = _obtain_password(args.email, args.password, args.password_env)
    if not password:
        log("err", "No password provided.")
        return 2

    spec = AccountSpec(
        email=args.email,
        password=password,
        alias=args.alias or "",
        output=args.output or "",
        proxy=args.proxy or "",
    )
    result = await login_and_save(
        spec,
        headless=not args.no_headless,
        proxy_url=args.proxy or os.getenv("HTTP_PROXY", ""),
        retry_count=args.retries,
        base_delay=args.retry_delay,
        register=args.register,
        register_path=expand_path(args.credentials_json),
        validate=not args.no_validate,
    )
    emit_result(result)
    return 0 if result.get("success") else 1


async def _cmd_batch(args: argparse.Namespace) -> int:
    try:
        specs = load_accounts_from_config(Path(args.config))
    except Exception as exc:
        log("err", f"Config load failed: {exc}")
        return 2
    specs = [s for s in specs if s.enabled]
    log("info", f"Loaded {len(specs)} enabled account(s) from {args.config}")

    results: list[dict] = []
    for i, spec in enumerate(specs, 1):
        log("step", f"[{i}/{len(specs)}] {spec.email}")
        res = await login_and_save(
            spec,
            headless=not args.no_headless,
            proxy_url=spec.proxy or args.proxy or os.getenv("HTTP_PROXY", ""),
            retry_count=args.retries,
            base_delay=args.retry_delay,
            register=args.register,
            register_path=expand_path(args.credentials_json),
            validate=not args.no_validate,
        )
        results.append(res)
        status = "ok" if res.get("success") else "err"
        detail = ("saved " + res.get("output", "")) if res.get("success") else res.get("error", "")
        log(status, f"[{i}/{len(specs)}] {spec.email}: {detail}")
        if i < len(specs):
            await asyncio.sleep(args.batch_cooldown)

    summary = {
        "total": len(results),
        "success": sum(1 for r in results if r.get("success")),
        "failed": sum(1 for r in results if not r.get("success")),
        "results": results,
    }
    emit_result(summary)
    return 0 if summary["failed"] == 0 else 1


async def _cmd_validate(args: argparse.Namespace) -> int:
    path = expand_path(args.credentials_file)
    if not path.exists():
        log("err", f"File not found: {path}")
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log("err", f"Cannot parse JSON: {exc}")
        return 2
    access_token = data.get("accessToken", "")
    profile_arn = data.get("profileArn", "")
    if not access_token:
        log("err", "No accessToken in file.")
        return 2
    credit = await fetch_kiro_usage(access_token, profile_arn)
    if credit and credit.get("_ok"):
        log("ok",
            f"Token valid. Credit: {credit.get('remainingCredits', 0):.0f}/"
            f"{credit.get('totalCredits', 0):.0f} ({credit.get('packageName','')})")
        emit_result({"success": True,
                     "credit": {k: v for k, v in credit.items() if not k.startswith("_")}})
        return 0
    log("err", f"Token invalid or validation failed: {credit}")
    emit_result({"success": False, "error": str(credit)})
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kiro_login.py",
        description="Automate Kiro OAuth PKCE login via Google; save Kiro-compatible credentials.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python kiro_login.py login --email me@gmail.com --register\n"
            "  python kiro_login.py login --email me@gmail.com --no-headless\n"
            "  python kiro_login.py batch --config accounts.json --register\n"
            "  python kiro_login.py validate --credentials-file ~/.aws/sso/cache/kiro-auto-me.json\n"
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--no-headless", action="store_true",
                        help="Launch browser visibly (to solve captcha/2FA manually)")
        sp.add_argument("--proxy", default="",
                        help="HTTP/SOCKS5 proxy URL (overrides $HTTP_PROXY)")
        sp.add_argument("--retries", type=int, default=DEFAULT_RETRY_COUNT,
                        help=f"Retry attempts on failure (default: {DEFAULT_RETRY_COUNT})")
        sp.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_BASE_DELAY,
                        help=f"Base delay between retries in seconds "
                             f"(default: {DEFAULT_RETRY_BASE_DELAY})")
        sp.add_argument("--register", action="store_true",
                        help="Register the account in kiro-gateway credentials.json")
        sp.add_argument("--credentials-json", default="credentials.json",
                        help="Path to kiro-gateway credentials.json "
                             "(default: ./credentials.json)")
        sp.add_argument("--no-validate", action="store_true",
                        help="Skip token validation against AWS Q /getUsageLimits")

    sp_login = sub.add_parser("login", help="Login a single account")
    sp_login.add_argument("--email", required=True, help="Google account email")
    sp_login.add_argument("--password", default=None,
                          help="Password (discouraged; prefer --password-env or "
                               "$KIRO_LOGIN_PASSWORD)")
    sp_login.add_argument("--password-env", default=None,
                          help="Env var name containing the password")
    sp_login.add_argument("--alias", default="", help="Short label (default: derived from email)")
    sp_login.add_argument("--output", default="",
                          help="Output credentials file path "
                               "(default: ~/.aws/sso/cache/kiro-auto-<alias>.json)")
    add_common(sp_login)

    sp_batch = sub.add_parser("batch", help="Login multiple accounts from a JSON config")
    sp_batch.add_argument("--config", required=True, help="Path to accounts.json")
    sp_batch.add_argument("--batch-cooldown", type=float, default=30.0,
                          help="Seconds to wait between accounts "
                               "(default: 30.0; anti-detection)")
    add_common(sp_batch)

    sp_val = sub.add_parser("validate",
                            help="Check whether a credentials file's token still works")
    sp_val.add_argument("--credentials-file", required=True,
                        help="Path to Kiro credentials JSON")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            return asyncio.run(_cmd_login(args))
        if args.command == "batch":
            return asyncio.run(_cmd_batch(args))
        if args.command == "validate":
            return asyncio.run(_cmd_validate(args))
    except KeyboardInterrupt:
        log("warn", "Interrupted by user.")
        return 130
    except Exception as exc:
        log("err", f"Fatal: {exc}")
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
