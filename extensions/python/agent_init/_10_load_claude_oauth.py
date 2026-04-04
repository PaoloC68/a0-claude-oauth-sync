import logging
import sys
import os

from helpers.extension import Extension

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


class LoadClaudeOAuth(Extension):
    async def execute(self, **kwargs):
        try:
            from claude_oauth_manager import get_valid_token
            from helpers.dotenv import save_dotenv_value

            token = get_valid_token()
            if token:
                save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                logger.info("[claude-oauth] Token loaded and injected into environment.")
            else:
                logger.warning("[claude-oauth] Could not obtain OAuth token at agent init.")
        except Exception as e:
            logger.warning("[claude-oauth] agent_init hook failed: %s", e)
