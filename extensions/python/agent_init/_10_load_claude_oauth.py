import logging
import sys
import os

from helpers.extension import Extension

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


class LoadClaudeOAuth(Extension):
    def execute(self, **kwargs):
        try:
            import claude_oauth_manager as m
            from helpers.dotenv import save_dotenv_value

            m.install_claude_cli()
            m.bootstrap_container_credentials()

            api_key = m.get_api_key_for_injection()
            if api_key:
                save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", api_key)
                logger.info("[claude-oauth] Derived API key injected (full rate limits).")
            else:
                token = m.get_valid_token()
                if token:
                    save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                    logger.info("[claude-oauth] OAuth token injected (fallback).")
                else:
                    logger.warning("[claude-oauth] Could not obtain OAuth token at agent init.")
        except Exception as e:
            logger.warning("[claude-oauth] agent_init hook failed: %s", e)
