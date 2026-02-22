"""Gateway Router for Penpot MCP.

This module acts as the hybrid router deciding whether to fulfill
an AI intent via Database/RPC calls (headless) or WebSocket (interactive).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PenpotGateway:
    """Intelligent router that decides the execution path (WS vs DB/API)."""

    def __init__(self):
        # We will initialize the WS controller here later
        from penpot_mcp.ws_controller import ws_controller

        self._ws = ws_controller

    @property
    def is_interactive(self) -> bool:
        """Returns True if the user is actively connected via the Plugin WebSocket."""
        return self._ws.is_connected

    @property
    def active_selection(self) -> list[str]:
        """Returns the UUIDs of the shapes currently selected by the user."""
        return self._ws.active_selection

    async def execute_intent(self, intent_name: str, **kwargs):
        """Execute an AI intent using the most effective path.

        Args:
            intent_name: Logical identity of the operation (e.g. 'get_page_objects')
            **kwargs: Arguments needed for the operation
        """
        # For Phase 1, we just fallback immediately to the DB/API implementations
        # which are currently located in penpot_mcp.tools.*

        # Example routing (to be expanded):
        if intent_name == "get_page_objects":
            from penpot_mcp.tools.shapes import get_page_objects

            return await get_page_objects(**kwargs)

        elif intent_name == "get_shape_tree":
            from penpot_mcp.tools.shapes import get_shape_tree

            return await get_shape_tree(**kwargs)

        else:
            raise NotImplementedError(f"Gateway has no route for intent: {intent_name}")


# Central singleton instance
gateway = PenpotGateway()
