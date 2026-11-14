"""Hybrid PenpotGateway for routing MCP and AI Studio intents across DB, RPC, and Live Plugin WS.

Unifies:
1. Fast PostgreSQL reads (asyncpg)
2. Safe Penpot RPC mutations (httpx)
3. Interactive Penpot Plugin WebSocket bridge (correlated script execution, live selection, viewport focus)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from penpot_mcp.ws_controller import ws_controller

logger = logging.getLogger(__name__)


class PenpotGateway:
    """Central hybrid router for Penpot MCP and Penpot AI Studio."""

    def __init__(self) -> None:
        self._interactive_override: bool | None = None
        self._selection_override: list[str] | None = None

    @property
    def is_interactive(self) -> bool:
        """Return True if a live Penpot Plugin connection is active (or overridden in tests)."""
        if self._interactive_override is not None:
            return self._interactive_override
        return ws_controller.is_connected

    @is_interactive.setter
    def is_interactive(self, value: bool | None) -> None:
        self._interactive_override = value

    @property
    def active_selection(self) -> list[str]:
        """Return currently selected shape UUIDs from the live Penpot Plugin."""
        if self._selection_override is not None:
            return self._selection_override
        return list(ws_controller.active_selection)

    @active_selection.setter
    def active_selection(self, value: list[str] | None) -> None:
        self._selection_override = value

    @property
    def active_context(self) -> dict[str, Any]:
        """Return the rich live canvas context (file_id, page_id, selection metadata, viewport)."""
        ctx = ws_controller.get_live_context()
        if self._interactive_override is not None:
            ctx["connected"] = self._interactive_override
        if self._selection_override is not None:
            ctx["selected_shape_ids"] = list(self._selection_override)
            ctx["selection_count"] = len(self._selection_override)
        return ctx

    async def execute_intent(self, intent_name: str, **kwargs: Any) -> Any:
        """Route an intent across interactive Plugin WS and headless DB/RPC tools."""
        logger.debug("Gateway routing intent '%s' (interactive=%s)", intent_name, self.is_interactive)

        if intent_name == "get_live_context":
            refresh = bool(kwargs.get("refresh", False))
            if refresh and ws_controller.is_connected:
                await ws_controller.request_context(timeout=float(kwargs.get("timeout", 3.0)))
            ctx = self.active_context
            # Fallback/enrich file_id & page_id if provided or discoverable
            if kwargs.get("file_id") and not ctx.get("file_id"):
                ctx["file_id"] = kwargs["file_id"]
            if kwargs.get("page_id") and not ctx.get("page_id"):
                ctx["page_id"] = kwargs["page_id"]
            return ctx

        if intent_name == "get_active_selection":
            if not self.is_interactive:
                return {
                    "error": "No active Penpot Plugin connection found. The user must have the plugin open."
                }
            ctx = self.active_context
            return {
                "selected_shape_ids": self.active_selection,
                "selection_count": len(self.active_selection),
                "selection": ctx.get("selection", []),
                "file_id": ctx.get("file_id"),
                "page_id": ctx.get("page_id"),
            }

        if intent_name == "execute_plugin_script":
            script = str(kwargs.get("script") or "")
            await_result = bool(kwargs.get("await_result", True))
            timeout = float(kwargs.get("timeout", 12.0))
            if not self.is_interactive:
                return {"error": "No active Penpot Plugin connection found."}
            if await_result:
                return await ws_controller.execute_script(script, timeout=timeout)
            ok = await ws_controller.send_command(script)
            return (
                {"status": "OK", "message": "Script executed (or broadcasted) successfully."}
                if ok
                else {"error": "Failed to send script."}
            )

        if intent_name == "focus_shapes":
            shape_ids = kwargs.get("shape_ids") or self.active_selection
            if not self.is_interactive:
                return {"error": "No active Penpot Plugin connection found."}
            ids_json = json.dumps([str(s) for s in (shape_ids or [])])
            focus_script = f"""
            const targetIds = new Set({ids_json});
            const page = penpot.currentPage;
            if (!page) return {{ focused: 0 }};
            const allShapes = page.findShapes ? page.findShapes({{}}) : [];
            const matched = allShapes.filter(s => targetIds.has(String(s.id)));
            if (matched.length > 0) {{
                penpot.selection = matched;
                if (penpot.viewport && typeof penpot.viewport.zoomIntoView === 'function') {{
                    penpot.viewport.zoomIntoView(matched);
                }}
            }}
            return {{ focused: matched.length, ids: matched.map(s => String(s.id)) }};
            """
            return await ws_controller.execute_script(focus_script, timeout=8.0)

        if intent_name == "get_page_objects":
            from penpot_mcp.tools.shapes import get_page_objects as _get_page_objects

            return await _get_page_objects(
                file_id=kwargs["file_id"],
                page_id=kwargs["page_id"],
                shape_type=kwargs.get("shape_type"),
            )

        if intent_name == "get_shape_tree":
            from penpot_mcp.tools.shapes import get_shape_tree as _get_shape_tree

            return await _get_shape_tree(
                file_id=kwargs["file_id"],
                page_id=kwargs["page_id"],
                root_id=kwargs.get("root_id"),
                depth=kwargs.get("depth", 3),
            )

        if intent_name == "get_shape_details":
            from penpot_mcp.tools.shapes import get_shape_details as _get_shape_details

            return await _get_shape_details(
                file_id=kwargs["file_id"],
                page_id=kwargs["page_id"],
                shape_id=kwargs["shape_id"],
            )

        if intent_name == "search_shapes":
            from penpot_mcp.tools.shapes import search_shapes as _search_shapes

            return await _search_shapes(
                file_id=kwargs["file_id"],
                page_id=kwargs["page_id"],
                query=kwargs["query"],
                search_type=kwargs.get("search_type", "name"),
            )

        if intent_name == "run_branch_tool":
            from penpot_mcp.tools.subtools import _build_batch_dispatch

            tool_name = str(kwargs.get("tool") or "")
            params = dict(kwargs.get("params") or {})
            dispatch = _build_batch_dispatch()
            fn = dispatch.get(tool_name)
            if fn is None:
                raise ValueError(f"Unknown branch tool '{tool_name}'")
            res = await fn(**params)
            return json.loads(res) if isinstance(res, str) else res

        if intent_name == "run_batch":
            from penpot_mcp.tools.subtools import playbook_run

            res = await playbook_run(
                action="code",
                name=kwargs.get("name", "gateway-batch"),
                steps=kwargs.get("steps", []),
                vars=kwargs.get("vars"),
                return_only=kwargs.get("return_only", "all"),
                select_fields=kwargs.get("select_fields"),
                verbosity=kwargs.get("verbosity", "summary"),
            )
            return json.loads(res) if isinstance(res, str) else res

        raise NotImplementedError(f"Intent '{intent_name}' not supported by PenpotGateway.")


# Singleton instance
gateway = PenpotGateway()
