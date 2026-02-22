"""WebSocket Controller for Penpot MCP Interactive Channel."""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

import websockets

from penpot_mcp.config import settings

logger = logging.getLogger(__name__)


class PenpotWSController:
    """Manages the WebSocket connection with the Penpot Plugin."""

    def __init__(self, host: str = "0.0.0.0", port: int = 4402):
        self.host = host
        self.port = port
        self.active_connections: set[Any] = set()
        self._server_task: Optional[asyncio.Task] = None

        # Phase 3 feature: Live Selection Context
        self.active_selection: list[str] = []

    @property
    def is_connected(self) -> bool:
        """Check if there is at least one active plugin connected."""
        return len(self.active_connections) > 0

    async def _handler(self, websocket):
        """Handle incoming WebSocket connections from the Penpot Plugin."""
        logger.info(f"New Plugin connection from {websocket.remote_address}")
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
            logger.error(f"WebSocket Error: {e}")
        finally:
            self.active_connections.remove(websocket)
            logger.info("Plugin connection closed.")

    def _handle_plugin_message(self, data: Dict[str, Any]):
        """Parse passive intents from the plugin (e.g., selection changes)."""
        msg_type = data.get("type")

        if msg_type == "selectionchange":
            # Phase 3: Selection Context Updates
            self.active_selection = data.get("ids", [])
            logger.debug(f"Live selection updated: {len(self.active_selection)} shapes")

        elif msg_type == "ack":
            logger.debug(f"Plugin ACK received for command {data.get('command_id')}")

    async def send_command(self, script: str) -> bool:
        """Send a JS script/command to be executed on the Plugin canvas.

        Returns True if sent successfully, False if offline.
        """
        if not self.is_connected:
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

    async def start(self):
        """Start the WebSocket server."""
        logger.info(f"Starting Plugin WebSocket Server on ws://{self.host}:{self.port}")
        start_server = websockets.serve(self._handler, self.host, self.port)
        self._server = await start_server

    async def stop(self):
        """Stop the WebSocket server."""
        if hasattr(self, "_server"):
            self._server.close()
            await self._server.wait_closed()
            logger.info("WebSocket Server stopped.")


# Singleton instance
ws_controller = PenpotWSController(host=settings.ws_host, port=settings.ws_port)
