import logging
import sys
import os

from helpers.api import ApiHandler, Request, Response

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


class ClaudeOAuthHandler(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "status")

        try:
            import claude_oauth_manager

            if action == "refresh":
                from helpers.dotenv import save_dotenv_value
                success = claude_oauth_manager.force_refresh()
                if success:
                    token = claude_oauth_manager.get_valid_token()
                    if token:
                        save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                return {"ok": success, "status": claude_oauth_manager.get_status()}

            return {"ok": True, "status": claude_oauth_manager.get_status()}

        except Exception as e:
            logger.warning("[claude-oauth] API handler error: %s", e)
            return {"ok": False, "error": str(e)}
