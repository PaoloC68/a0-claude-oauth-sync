import logging
import sys
import os
import threading

from helpers.extension import Extension

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

_session_established = False
_session_lock = threading.Lock()


class LoadClaudeOAuth(Extension):
    def execute(self, **kwargs):
        global _session_established
        try:
            import claude_oauth_manager as m
            from helpers.dotenv import save_dotenv_value

            m.install_claude_cli()
            m.bootstrap_container_credentials()

            # Establish CLI session once per process to bless the token for sonnet/opus.
            # Runs only on the first agent_init call — subsequent chat loads skip it.
            with _session_lock:
                if not _session_established and m._is_docker() and m._find_claude_bin():
                    logger.info("[claude-oauth] Establishing CLI session (first init only)...")
                    m._refresh_via_cli()
                    creds = m._read_credentials()
                    if creds:
                        m._update_cache(creds)
                    _session_established = True

            token = m.get_valid_token()
            if token:
                save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                logger.info("[claude-oauth] Token injected.")
            else:
                logger.warning("[claude-oauth] Could not obtain OAuth token at agent init.")
        except Exception as e:
            logger.warning("[claude-oauth] agent_init hook failed: %s", e)
