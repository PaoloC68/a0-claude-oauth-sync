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

            # Always run the CLI once to establish a live session at Anthropic's backend.
            # This "blesses" the token, enabling sonnet/opus access — without it,
            # only haiku works via direct API calls.
            if m._is_docker() and m._find_claude_bin():
                logger.info("[claude-oauth] Establishing CLI session via haiku ping...")
                m._refresh_via_cli()
                creds = m._read_credentials()
                if creds:
                    m._update_cache(creds)

            token = m.get_valid_token()
            if token:
                save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                logger.info("[claude-oauth] Token injected after CLI session established.")
            else:
                logger.warning("[claude-oauth] Could not obtain OAuth token at agent init.")
        except Exception as e:
            logger.warning("[claude-oauth] agent_init hook failed: %s", e)
