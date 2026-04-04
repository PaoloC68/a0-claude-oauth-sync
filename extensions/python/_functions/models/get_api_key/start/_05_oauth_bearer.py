import logging
import sys
import os

from helpers.extension import Extension

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), *(['..'] * 6))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, os.path.normpath(PLUGIN_DIR))


class OAuthBearerInjector(Extension):
    def execute(self, data, **kwargs):
        args = data.get("args", ())
        if not args or args[0] != "anthropic_oauth":
            return
        try:
            import claude_oauth_manager as m
            token = m.get_valid_token()
            if token:
                data["result"] = token
        except Exception as e:
            logger.debug("[claude-oauth] Bearer injector: %s", e)
