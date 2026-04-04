import logging
import sys
import os

from helpers.api import ApiHandler, Request, Response

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


class ClaudeLoginHandler(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "start")

        try:
            import claude_oauth_manager as m
            from helpers.dotenv import save_dotenv_value

            if action == "start":
                result = m.start_oauth_login()
                return {"ok": True, **result}

            if action == "complete":
                code = input.get("code", "").strip()
                if not code:
                    return {"ok": False, "error": "No code provided"}

                success, error = m.complete_oauth_login(code)
                if success:
                    token = m.get_valid_token()
                    if token:
                        save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                return {"ok": success, "error": error if not success else "", "status": m.get_status()}

            return {"ok": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.warning("[claude-oauth] Login handler error: %s", e)
            return {"ok": False, "error": str(e)}
