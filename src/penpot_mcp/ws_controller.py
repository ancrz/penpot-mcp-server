"""WebSocket Controller for Penpot MCP Interactive Channel."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import json
import logging
import secrets
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit
import uuid

import websockets

from penpot_mcp.config import settings

logger = logging.getLogger(__name__)


class PenpotWSController:
    """Manages the WebSocket connection with the Penpot Plugin."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4402):
        self.host = host
        self.port = port
        self.active_connections: set[Any] = set()
        self._server_task: Optional[asyncio.Task] = None
        self._server: Any = None

        # Phase 3 feature: Live Selection Context
        self.active_selection: list[str] = []

    @property
    def is_connected(self) -> bool:
        """Check if there is at least one active plugin connected."""
        return len(self.active_connections) > 0

    def _check_token(self, connection: Any, request: Any) -> Any:
        """Validate the shared WebSocket token during the HTTP upgrade handshake."""
        expected_token = settings.ws_token
        if not expected_token:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")

        path = getattr(request, "path", "") or ""
        token = parse_qs(urlsplit(path).query).get("token", [""])[0]
        if not token or not secrets.compare_digest(token, expected_token):
            logger.warning("Rejected WebSocket connection with invalid or missing token")
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        return None

    async def _handler(self, websocket: Any) -> None:
        """Handle incoming WebSocket connections from the Penpot Plugin."""
        logger.info("New Plugin connection from %s", websocket.remote_address)
        self.active_connections.add(websocket)

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    self._handle_plugin_message(data)
                except json.JSONDecodeError:
                    logger.warning("Received non-JSON message from plugin.")
        except websockets.exceptions.ConnectionClosed:
            logger.info("Plugin disconnected normally.")
        except Exception as e:
            logger.error("WebSocket Error: %s", e)
        finally:
            self.active_connections.discard(websocket)
            logger.info("Plugin connection closed.")

    def _handle_plugin_message(self, data: Dict[str, Any]) -> None:
        """Parse passive intents from the plugin (e.g., selection changes)."""
        if not isinstance(data, dict):
            return
        msg_type = data.get("type")

        if msg_type == "selectionchange":
            # Phase 3: Selection Context Updates (bounded & type-checked)
            raw_ids = data.get("ids", [])
            if isinstance(raw_ids, list):
                self.active_selection = [
                    str(item) for item in raw_ids[:1000] if isinstance(item, str)
                ]
            logger.debug("Live selection updated: %d shapes", len(self.active_selection))

        elif msg_type == "ack":
            logger.debug("Plugin ACK received for command %s", data.get("command_id"))

    async def send_command(self, script: str) -> bool:
        """Send a JS script/command to be executed on the Plugin canvas.

        Returns True if sent successfully, False if offline.
        """
        if not self.is_connected or not (script or "").strip():
            return False

        payload = json.dumps(
            {"type": "execute", "command_id": str(uuid.uuid4()), "script": script}
        )

        # Broadcast to all connected plugins (usually just 1)
        for ws in list(self.active_connections):
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                pass

        return True

    async def start(self) -> None:
        """Start the WebSocket server if enabled and configured with a token."""
        if not settings.ws_enabled:
            logger.info(
                "Plugin WebSocket bridge disabled (set WS_ENABLED=true and WS_TOKEN to enable)"
            )
            return
        if not settings.ws_token or len(settings.ws_token.strip()) < 8:
            logger.warning(
                "WS_ENABLED=true but WS_TOKEN is empty or too short (<8 chars); refusing to start WebSocket bridge"
            )
            return
        if settings.ws_host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "Refusing to bind Plugin WebSocket bridge to non-loopback host %s",
                settings.ws_host,
            )
            return

        self.host = settings.ws_host
        self.port = settings.ws_port
        logger.info(
            "Starting Plugin WebSocket Server on %s:%d (origin=%s)",
            self.host,
            self.port,
            settings.plugin_origin,
        )
        self._server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            origins=[websockets.Origin(settings.plugin_origin)],
            process_request=self._check_token,
            max_size=1_048_576,
        )

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("WebSocket Server stopped.")


# Singleton instance
ws_controller = PenpotWSController(host=settings.ws_host, port=settings.ws_port)
