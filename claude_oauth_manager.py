from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
from typing import TypedDict

logger = logging.getLogger(__name__)

_REFRESH_BUFFER_MS = 5 * 60 * 1000
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")
_NPM_PREFIX = "/a0/usr/.npm-packages"

_FIELDS_TO_PRESERVE = ("accessToken", "refreshToken", "expiresAt", "subscriptionType", "rateLimitTier", "scopes")

_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_OAUTH_MANUAL_REDIRECT = "https://console.anthropic.com/oauth/code/callback"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"


class TokenInfo(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: int
    subscription_type: str


_cache: TokenInfo | None = None
_cache_lock = threading.Lock()
_cli_available: bool | None = None

_pending_login: dict | None = None


def get_valid_token() -> str | None:
    with _cache_lock:
        return _get_valid_token_locked()


def get_status() -> dict:
    with _cache_lock:
        info = _get_valid_token_locked()
        in_docker = _is_docker()
        has_own_session = _has_own_session()
        cli = bool(_find_claude_bin())

        if _cache is None:
            return {
                "status": "unavailable",
                "message": "No credentials found.",
                "in_docker": in_docker,
                "own_session": has_own_session,
                "cli_installed": cli,
            }

        now_ms = _now_ms()
        remaining_ms = _cache["expires_at"] - now_ms
        return {
            "status": "valid" if remaining_ms > 0 else "expired",
            "access_token_prefix": _cache["access_token"][:20] + "..." if _cache.get("access_token") else None,
            "expires_at": _cache["expires_at"],
            "expires_in_minutes": max(0, int(remaining_ms / 60_000)),
            "subscription_type": _cache.get("subscription_type", "unknown"),
            "token_obtained": info is not None,
            "cli_installed": cli,
            "in_docker": in_docker,
            "own_session": has_own_session,
        }


def force_refresh() -> bool:
    with _cache_lock:
        if _refresh_via_api():
            return True
        if not _refresh_via_cli():
            return False
        creds = _read_credentials()
        if not creds:
            return False
        _update_cache(creds)
        return True


def start_oauth_login() -> dict:
    global _pending_login
    code_verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": _OAUTH_SCOPES,
        "redirect_uri": _OAUTH_MANUAL_REDIRECT,
        "state": state,
    }
    auth_url = _OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    _pending_login = {"code_verifier": code_verifier, "state": state, "started_at": _now_ms()}
    logger.info("[claude-oauth] OAuth login started.")
    return {"auth_url": auth_url, "state": state}


def complete_oauth_login(code: str) -> tuple[bool, str]:
    global _pending_login
    if not _pending_login:
        return False, "Session expired. Click 'Connect Container' again to restart."

    age_ms = _now_ms() - _pending_login.get("started_at", 0)
    if age_ms > 10 * 60 * 1000:
        _pending_login = None
        return False, "Login session expired (10 min limit). Click 'Connect Container' again."

    clean_code = code.strip().split("#")[0].split("&")[0].strip()
    if not clean_code:
        return False, "Empty code after stripping. Please copy just the code value."

    code_verifier = _pending_login["code_verifier"]
    state = _pending_login["state"]
    payload = json.dumps({
        "grant_type": "authorization_code",
        "code": clean_code,
        "code_verifier": code_verifier,
        "client_id": _OAUTH_CLIENT_ID,
        "redirect_uri": _OAUTH_MANUAL_REDIRECT,
        "state": state,
    }).encode()

    req = urllib.request.Request(
        _OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        new_creds = {
            "accessToken": data["access_token"],
            "refreshToken": data.get("refresh_token", ""),
            "expiresAt": int(time.time() * 1000) + int(data.get("expires_in", 18000)) * 1000,
            "subscriptionType": data.get("account", {}).get("subscription_type", "unknown"),
            "container_session": True,
        }
        with _cache_lock:
            _update_cache(new_creds)
        _write_container_creds_file(new_creds)
        _pending_login = None
        logger.info("[claude-oauth] Container login complete. Independent session established.")
        return True, ""

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        logger.warning("[claude-oauth] Login exchange failed (%s): %s", e.code, body)
        return False, f"Anthropic error {e.code}: {body}"
    except Exception as e:
        logger.warning("[claude-oauth] Login exchange error: %s", e)
        return False, str(e)


def install_claude_cli() -> bool:
    global _cli_available
    if _cli_available is None:
        _cli_available = bool(_find_claude_bin())
    if _cli_available:
        return True
    if not _is_docker():
        logger.warning("[claude-oauth] Claude CLI not found. Install @anthropic-ai/claude-code manually.")
        return False

    logger.info("[claude-oauth] Installing Node.js and Claude CLI (persistent prefix: %s)...", _NPM_PREFIX)

    apt = subprocess.run(
        ["apt-get", "install", "-y", "-q", "nodejs", "npm"],
        timeout=180, capture_output=True,
    )
    if apt.returncode != 0:
        logger.warning("[claude-oauth] nodejs/npm install failed: %s", apt.stderr.decode(errors="replace")[:300])
        return False

    npm = subprocess.run(
        ["npm", "install", "-g", "--prefix", _NPM_PREFIX, "@anthropic-ai/claude-code"],
        timeout=180, capture_output=True,
    )
    if npm.returncode != 0:
        logger.warning("[claude-oauth] claude CLI install failed: %s", npm.stderr.decode(errors="replace")[:300])
        return False

    _cli_available = bool(_find_claude_bin())
    if _cli_available:
        logger.info("[claude-oauth] Claude CLI installed to %s.", _NPM_PREFIX)
    return _cli_available


def bootstrap_container_credentials() -> bool:
    if not _is_docker():
        return True

    existing = _read_from_file()
    if existing:
        oauth = existing.get("claudeAiOauth") or existing
        if oauth.get("expiresAt", 0) > _now_ms():
            return True

    cached = _read_from_cache_file() or _read_from_env()
    if not cached:
        logger.warning("[claude-oauth] No credentials to bootstrap container with.")
        return False

    _write_container_creds_file(cached)
    return True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _has_own_session() -> bool:
    cached = _read_from_cache_file()
    return bool(cached and cached.get("container_session"))


def _get_creds_cache_path() -> str:
    try:
        from helpers.files import get_abs_path
        return get_abs_path("usr/.claude-oauth-creds.json")
    except Exception:
        return os.path.expanduser("~/.claude-oauth-creds.json")


def _read_from_cache_file() -> dict | None:
    path = _get_creds_cache_path()
    try:
        with open(path) as f:
            data = json.load(f)
        inner = data.get("claudeAiOauth") or data
        if inner.get("accessToken"):
            if data.get("container_session"):
                inner["container_session"] = True
            return inner
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return None


def _write_cache_file(oauth: dict) -> None:
    path = _get_creds_cache_path()
    try:
        payload = {k: oauth[k] for k in _FIELDS_TO_PRESERVE if k in oauth}
        if oauth.get("container_session"):
            payload["container_session"] = True
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as e:
        logger.debug("[claude-oauth] Failed to write cache file: %s", e)


def _write_container_creds_file(oauth: dict) -> None:
    creds_file = _CREDENTIALS_FILE
    try:
        os.makedirs(os.path.dirname(creds_file), exist_ok=True)
        payload = {"claudeAiOauth": {k: oauth[k] for k in _FIELDS_TO_PRESERVE if k in oauth}}
        tmp = creds_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, creds_file)
    except OSError as e:
        logger.warning("[claude-oauth] Failed to write container credentials: %s", e)


def _get_valid_token_locked() -> str | None:
    global _cache
    now_ms = _now_ms()

    if _cache and _cache["expires_at"] > now_ms + _REFRESH_BUFFER_MS:
        return _cache["access_token"]

    creds = _read_credentials()
    if not creds:
        logger.warning("[claude-oauth] No credentials found.")
        return None

    expires_at = creds.get("expiresAt", 0)
    if expires_at > now_ms + _REFRESH_BUFFER_MS:
        _update_cache(creds)
        return creds["accessToken"]

    label = "Token near expiry" if _cache else "Loading initial token"
    logger.info("[claude-oauth] %s — refreshing...", label)

    if _refresh_via_api():
        return _cache["access_token"] if _cache else None

    logger.warning("[claude-oauth] Token refresh failed.")
    if _cache and _cache["access_token"]:
        logger.warning("[claude-oauth] Returning stale token as fallback.")
        return _cache["access_token"]

    return None


def _update_cache(creds: dict) -> None:
    global _cache
    oauth = creds.get("claudeAiOauth") or creds
    _cache = {
        "access_token": oauth.get("accessToken", ""),
        "refresh_token": oauth.get("refreshToken", ""),
        "expires_at": oauth.get("expiresAt", 0),
        "subscription_type": oauth.get("subscriptionType", "unknown"),
    }
    _write_cache_file(oauth)


def _refresh_via_api() -> bool:
    cached = _read_from_cache_file()
    if not cached or not cached.get("container_session"):
        logger.debug("[claude-oauth] Skipping API refresh: container has no own session yet.")
        return False

    refresh_token = cached.get("refreshToken")
    if not refresh_token:
        return False

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _OAUTH_CLIENT_ID,
        "scope": _OAUTH_SCOPES,
    }).encode()

    req = urllib.request.Request(
        _OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        new_creds = {
            "accessToken": data["access_token"],
            "refreshToken": data.get("refresh_token", refresh_token),
            "expiresAt": int(time.time() * 1000) + int(data.get("expires_in", 18000)) * 1000,
            "subscriptionType": cached.get("subscriptionType", "unknown"),
            "rateLimitTier": cached.get("rateLimitTier", ""),
            "scopes": cached.get("scopes", []),
            "container_session": True,
        }
        _update_cache(new_creds)
        _write_container_creds_file(new_creds)
        logger.info("[claude-oauth] Token refreshed. Valid for %d min.", data.get("expires_in", 18000) // 60)
        return True

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        logger.warning("[claude-oauth] OAuth API refresh failed (%s): %s", e.code, body)
    except Exception as e:
        logger.warning("[claude-oauth] OAuth API refresh error: %s", e)
    return False


def _read_credentials() -> dict | None:
    raw = None
    if sys.platform == "darwin":
        raw = _read_from_keychain()
    if raw is None:
        raw = _read_from_file()
    if raw is None:
        if _is_docker():
            raw = _read_from_cache_file() or _read_from_env()
        else:
            raw = _read_from_cache_file() or _read_from_env()
    if raw is None:
        return None
    if "claudeAiOauth" in raw:
        return raw["claudeAiOauth"]
    return raw


def _read_from_keychain() -> dict | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        if result.returncode == 36:
            logger.warning("[claude-oauth] macOS Keychain is locked.")
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        logger.debug("[claude-oauth] Keychain read failed: %s", e)
    return None


def _read_from_file() -> dict | None:
    path = os.environ.get("CLAUDE_CREDENTIALS_PATH", _CREDENTIALS_FILE)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.debug("[claude-oauth] Credentials file read failed (%s): %s", path, e)
    return None


def _read_from_env() -> dict | None:
    access = os.environ.get("ANTHROPIC_OAUTH_ACCESS_TOKEN") or os.environ.get("API_KEY_ANTHROPIC_OAUTH")
    refresh = os.environ.get("ANTHROPIC_OAUTH_REFRESH_TOKEN")
    expires = os.environ.get("ANTHROPIC_OAUTH_EXPIRES_AT")
    if not (access and refresh and expires):
        return None
    try:
        return {
            "accessToken": access,
            "refreshToken": refresh,
            "expiresAt": int(expires),
            "subscriptionType": os.environ.get("ANTHROPIC_OAUTH_SUBSCRIPTION_TYPE", "unknown"),
        }
    except ValueError:
        return None


def _refresh_via_cli() -> bool:
    """Run a no-op claude prompt to trigger CLI's internal token refresh.
    Exit 0 = output produced, exit 1 = no output — both indicate success.
    """
    claude_bin = _find_claude_bin()
    if not claude_bin:
        return False
    try:
        result = subprocess.run(
            [claude_bin, "-p", ".", "--model", "claude-haiku-4-5"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=90,
            env={**os.environ, "NODE_PATH": f"{_NPM_PREFIX}/lib/node_modules"},
        )
        return result.returncode in (0, 1)
    except subprocess.TimeoutExpired:
        logger.warning("[claude-oauth] Claude CLI refresh timed out.")
    except (FileNotFoundError, OSError) as e:
        logger.warning("[claude-oauth] Claude CLI refresh error: %s", e)
    return False


def _find_claude_bin() -> str | None:
    candidates = [
        f"{_NPM_PREFIX}/bin/claude",
        os.path.expanduser("~/.npm-global/bin/claude"),
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.volta/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
        "/usr/local/lib/node_modules/.bin/claude",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    try:
        result = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
