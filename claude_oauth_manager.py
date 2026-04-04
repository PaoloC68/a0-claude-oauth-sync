from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import threading
from typing import TypedDict

logger = logging.getLogger(__name__)

_REFRESH_BUFFER_MS = 5 * 60 * 1000
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")


class TokenInfo(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: int
    subscription_type: str


_cache: TokenInfo | None = None
_cache_lock = threading.Lock()


def get_valid_token() -> str | None:
    with _cache_lock:
        return _get_valid_token_locked()


def get_status() -> dict:
    with _cache_lock:
        info = _get_valid_token_locked()
        if _cache is None:
            return {"status": "unavailable", "message": "No Claude CLI credentials found."}

        now_ms = _now_ms()
        remaining_ms = _cache["expires_at"] - now_ms
        return {
            "status": "valid" if remaining_ms > 0 else "expired",
            "access_token_prefix": _cache["access_token"][:20] + "..." if _cache.get("access_token") else None,
            "expires_at": _cache["expires_at"],
            "expires_in_minutes": max(0, int(remaining_ms / 60_000)),
            "subscription_type": _cache.get("subscription_type", "unknown"),
            "token_obtained": info is not None,
        }


def force_refresh() -> bool:
    with _cache_lock:
        logger.info("[claude-oauth] Force refresh requested")
        if not _refresh_via_cli():
            return False
        creds = _read_credentials()
        if not creds:
            return False
        _update_cache(creds)
        return True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_valid_token_locked() -> str | None:
    global _cache
    now_ms = _now_ms()

    if _cache and _cache["expires_at"] > now_ms + _REFRESH_BUFFER_MS:
        return _cache["access_token"]

    creds = _read_credentials()
    if not creds:
        logger.warning("[claude-oauth] No credentials found from Keychain or file.")
        return None

    expires_at = creds.get("expiresAt", 0)
    if expires_at > now_ms + _REFRESH_BUFFER_MS:
        _update_cache(creds)
        return creds["accessToken"]

    label = "Token near expiry" if _cache else "Loading initial token"
    logger.info("[claude-oauth] %s — refreshing via Claude CLI...", label)

    if _refresh_via_cli():
        creds = _read_credentials()
        if creds and creds.get("expiresAt", 0) > now_ms:
            _update_cache(creds)
            return creds["accessToken"]
        logger.warning("[claude-oauth] Refresh succeeded but new token still invalid.")
    else:
        logger.warning("[claude-oauth] Token refresh via Claude CLI failed.")
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


def _read_credentials() -> dict | None:
    raw = None
    if sys.platform == "darwin":
        raw = _read_from_keychain()
    if raw is None:
        raw = _read_from_file()
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


def _refresh_via_cli() -> bool:
    """Run a no-op claude prompt to trigger CLI's internal token refresh.
    Exit 0 = output produced, exit 1 = no output — both indicate success.
    """
    claude_bin = _find_claude_bin()
    if not claude_bin:
        logger.warning("[claude-oauth] 'claude' binary not found in PATH.")
        return False
    try:
        result = subprocess.run(
            [claude_bin, "-p", ".", "--model", "claude-haiku-4-5"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )
        return result.returncode in (0, 1)
    except subprocess.TimeoutExpired:
        logger.warning("[claude-oauth] Claude CLI refresh timed out after 90s.")
    except (FileNotFoundError, OSError) as e:
        logger.warning("[claude-oauth] Claude CLI refresh error: %s", e)
    return False


def _find_claude_bin() -> str | None:
    candidates = [
        os.path.expanduser("~/.npm-global/bin/claude"),
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
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
