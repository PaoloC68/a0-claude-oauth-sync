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
            import asyncio
            import claude_oauth_manager as m
            from helpers.dotenv import save_dotenv_value

            if action == "refresh":
                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(None, m.force_refresh)
                if success:
                    token = await loop.run_in_executor(None, m.get_valid_token)
                    if token:
                        save_dotenv_value("API_KEY_ANTHROPIC_OAUTH", token)
                return {"ok": success, "status": m.get_status()}

            if action == "install":
                loop = asyncio.get_event_loop()
                installed = await loop.run_in_executor(None, m.install_claude_cli)
                bootstrapped = await loop.run_in_executor(None, m.bootstrap_container_credentials)
                return {"ok": installed, "cli_installed": installed, "credentials_bootstrapped": bootstrapped, "status": m.get_status()}

            return {"ok": True, "status": m.get_status()}

        except Exception as e:
            logger.warning("[claude-oauth] API handler error: %s", e)
            return {"ok": False, "error": str(e)}
