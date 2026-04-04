import logging
import sys
import os
import time

from helpers.extension import Extension

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

_REFRESH_THRESHOLD_MS = 10 * 60 * 1000


class RefreshClaudeOAuth(Extension):
    async def execute(self, **kwargs):
        try:
            import claude_oauth_manager as m
            from helpers.dotenv import save_dotenv_value

            cached = m._cache
            if cached is None:
                return

            now_ms = int(time.time() * 1000)
            if cached["expires_at"] - now_ms > _REFRESH_THRESHOLD_MS:
                return

            token = m.get_valid_token()
            if token:
                save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                logger.info("[claude-oauth] Token refreshed at monologue_start.")
        except Exception as e:
            logger.warning("[claude-oauth] monologue_start refresh failed: %s", e)
