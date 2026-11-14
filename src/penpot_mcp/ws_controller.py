"""WebSocket & Telemetry Controller for Penpot MCP Interactive Plugin Channel.

Fuses Official Penpot MCP's bidirectional task correlation (command_id -> Future)
with live canvas context telemetry (file_id, page_id, selection metadata, viewport)
across both same-origin Starlette WebSockets (/plugin/ws on :8787), standalone
WebSockets (:4402), and HTTP telemetry sync (/plugin/telemetry).
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit
import uuid

import websockets

from penpot_mcp.config import settings

logger = logging.getLogger(__name__)


class PenpotWSController:
    """Manages bidirectional communication and request/response futures with the Penpot Plugin."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4402):
        self.host = host
        self.port = port
        self.active_connections: set[Any] = set()
        self._server_task: Optional[asyncio.Task[Any]] = None
        self._server: Any = None

        # Live Selection & Rich Canvas Context
        self.active_selection: list[str] = []
        self.active_context: dict[str, Any] = {
            "file_id": None,
            "file_name": None,
            "page_id": None,
            "page_name": None,
            "selection": [],
            "viewport": {},
            "updated_at": None,
        }
        self.last_telemetry_at: float = 0.0

        # Correlated Request/Response Futures (command_id -> asyncio.Future)
        self._pending_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Queue of commands for HTTP telemetry fallback clients
        self._http_command_queue: list[dict[str, Any]] = []

    @property
    def is_connected(self) -> bool:
        """Return True if at least one WS client is connected or HTTP telemetry is active (<30s)."""
        if len(self.active_connections) > 0:
            return True
        if self.last_telemetry_at > 0 and (time.monotonic() - self.last_telemetry_at) < 30.0:
            return True
        return False

    def get_live_context(self) -> dict[str, Any]:
        """Return a snapshot of the current live plugin connection and canvas state."""
        return {
            "connected": self.is_connected,
            "ws_clients": len(self.active_connections),
            "file_id": self.active_context.get("file_id"),
            "file_name": self.active_context.get("file_name"),
            "page_id": self.active_context.get("page_id"),
            "page_name": self.active_context.get("page_name"),
            "selected_shape_ids": list(self.active_selection),
            "selection_count": len(self.active_selection),
            "selection": list(self.active_context.get("selection") or []),
            "viewport": dict(self.active_context.get("viewport") or {}),
            "updated_at": self.active_context.get("updated_at"),
        }

    def validate_token_string(self, token: str | None) -> bool:
        """Validate a token string against settings.ws_token (allows empty token only when ws_token is unset)."""
        expected_token = (settings.ws_token or "").strip()
        if not expected_token:
            return False
        if not token:
            return False
        return secrets.compare_digest(str(token), expected_token)

    def _check_token(self, connection: Any, request: Any) -> Any:
        """Validate the shared WebSocket token during the standalone HTTP upgrade handshake."""
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
        """Handle incoming standalone WebSocket connections from the Penpot Plugin."""
        logger.info("New Plugin WS connection from %s", getattr(websocket, "remote_address", "client"))
        self.active_connections.add(websocket)
        self.last_telemetry_at = time.monotonic()

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

    async def handle_starlette_ws(self, websocket: Any) -> None:
        """Handle same-origin Starlette WebSocket connections on /plugin/ws (:8787)."""
        query_params = getattr(websocket, "query_params", {}) or {}
        token = query_params.get("token", "")
        if settings.ws_token and not self.validate_token_string(token):
            await websocket.close(code=1008)
            return

        await websocket.accept()
        self.active_connections.add(websocket)
        self.last_telemetry_at = time.monotonic()
        logger.info("Accepted same-origin Plugin WebSocket on /plugin/ws")

        try:
            while True:
                raw_text = await websocket.receive_text()
                try:
                    data = json.loads(raw_text)
                    self._handle_plugin_message(data)
                except json.JSONDecodeError:
                    logger.warning("Received non-JSON message on /plugin/ws.")
        except Exception:
            pass
        finally:
            self.active_connections.discard(websocket)
            logger.info("Closed same-origin Plugin WebSocket on /plugin/ws")

    def record_http_telemetry(self, data: Dict[str, Any]) -> list[dict[str, Any]]:
        """Process an HTTP telemetry/poll payload from ui.html and return any queued commands."""
        self.last_telemetry_at = time.monotonic()
        self._handle_plugin_message(data)
        queued = list(self._http_command_queue)
        self._http_command_queue.clear()
        return queued

    def _handle_plugin_message(self, data: Dict[str, Any]) -> None:
        """Parse telemetry, selection updates, and command results from the plugin."""
        if not isinstance(data, dict):
            return
        self.last_telemetry_at = time.monotonic()
        msg_type = data.get("type")

        if msg_type in ("selectionchange", "context", "telemetry"):
            raw_ids = data.get("ids")
            raw_shapes = data.get("selection")
            if isinstance(raw_ids, list):
                self.active_selection = [
                    str(item) for item in raw_ids[:1000] if isinstance(item, str)
                ]
            elif isinstance(raw_shapes, list):
                self.active_selection = [
                    str(item.get("id"))
                    for item in raw_shapes[:1000]
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]

            if isinstance(raw_shapes, list):
                sanitized_shapes: list[dict[str, Any]] = []
                for s in raw_shapes[:200]:
                    if isinstance(s, dict) and s.get("id"):
                        sanitized_shapes.append(
                            {
                                "id": str(s.get("id")),
                                "name": str(s.get("name") or "Shape"),
                                "type": str(s.get("type") or "shape"),
                                "x": s.get("x"),
                                "y": s.get("y"),
                                "width": s.get("width"),
                                "height": s.get("height"),
                                "fills": s.get("fills"),
                            }
                        )
                self.active_context["selection"] = sanitized_shapes

            for key in ("file_id", "file_name", "page_id", "page_name"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    self.active_context[key] = val.strip()

            if isinstance(data.get("viewport"), dict):
                self.active_context["viewport"] = data["viewport"]

            self.active_context["updated_at"] = time.time()
            logger.debug("Live context updated: %d selected shapes", len(self.active_selection))

        if msg_type in ("ack", "result"):
            cmd_id = str(data.get("command_id") or "")
            logger.debug("Plugin response (%s) received for command %s", msg_type, cmd_id)
            fut = self._pending_futures.get(cmd_id)
            if fut is not None and not fut.done():
                fut.set_result(data)

    async def _broadcast_payload(self, payload_dict: dict[str, Any]) -> bool:
        """Broadcast a JSON command dictionary to all active WS connections or HTTP poll queue."""
        payload_str = json.dumps(payload_dict)
        sent = False
        for ws in list(self.active_connections):
            try:
                if hasattr(ws, "send_text"):
                    await ws.send_text(payload_str)
                else:
                    await ws.send(payload_str)
                sent = True
            except Exception:
                self.active_connections.discard(ws)

        if not sent and self.is_connected:
            # Queue for HTTP telemetry polling client
            self._http_command_queue.append(payload_dict)
            if len(self._http_command_queue) > 50:
                self._http_command_queue = self._http_command_queue[-50:]
            sent = True
        return sent

    async def send_command(self, script: str) -> bool:
        """Send a JS script/command to be executed on the Plugin canvas (fire-and-forget).

        Returns True if sent successfully, False if offline.
        """
        if not self.is_connected or not (script or "").strip():
            return False

        payload_dict = {
            "type": "execute",
            "command_id": str(uuid.uuid4()),
            "script": script,
        }
        return await self._broadcast_payload(payload_dict)

    async def execute_script(self, script: str, timeout: float = 12.0) -> dict[str, Any]:
        """Execute a JS script in the Penpot Plugin and await its correlated return value."""
        if not self.is_connected:
            return {"status": "ERROR", "error": "No active Penpot Plugin connection found."}
        if not (script or "").strip():
            return {"status": "PARAM_ERROR", "error": "Script must not be empty."}

        cmd_id = f"cmd_{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_futures[cmd_id] = fut

        started = time.monotonic()
        payload_dict = {
            "type": "execute",
            "command_id": cmd_id,
            "script": script,
        }
        try:
            sent = await self._broadcast_payload(payload_dict)
            if not sent:
                return {"status": "ERROR", "error": "Failed to dispatch script to plugin."}

            response = await asyncio.wait_for(fut, timeout=timeout)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            status_raw = str(response.get("status") or "ok").lower()
            if status_raw == "error" or "error" in response:
                return {
                    "status": "ERROR",
                    "command_id": cmd_id,
                    "duration_ms": duration_ms,
                    "error": str(response.get("error") or "Plugin execution failed"),
                }
            return {
                "status": "OK",
                "command_id": cmd_id,
                "duration_ms": duration_ms,
                "result": response.get("result"),
            }
        except asyncio.TimeoutError:
            return {
                "status": "PARTIAL",
                "command_id": cmd_id,
                "message": "Script dispatched to plugin; timed out waiting for return payload.",
            }
        finally:
            self._pending_futures.pop(cmd_id, None)

    async def request_context(self, timeout: float = 4.0) -> dict[str, Any]:
        """Request an immediate context refresh from the connected plugin."""
        if not self.is_connected:
            return self.get_live_context()

        cmd_id = f"ctx_{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_futures[cmd_id] = fut
        try:
            await self._broadcast_payload({"type": "get_context", "command_id": cmd_id})
            await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            pass
        finally:
            self._pending_futures.pop(cmd_id, None)
        return self.get_live_context()

    async def start(self) -> None:
        """Start the standalone WebSocket server on WS_PORT if enabled and configured with a token."""
        if not settings.ws_enabled:
            logger.info(
                "Standalone Plugin WebSocket server (:4402) disabled (same-origin /plugin/ws on :8787 remains available when WS_TOKEN is set)"
            )
            return
        if not settings.ws_token or len(settings.ws_token.strip()) < 8:
            logger.warning(
                "WS_ENABLED=true but WS_TOKEN is empty or too short (<8 chars); refusing to start WebSocket bridge"
            )
            return
        if settings.ws_host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            logger.warning(
                "Refusing to bind Plugin WebSocket bridge to unexpected host %s",
                settings.ws_host,
            )
            return

        self.host = settings.ws_host
        self.port = settings.ws_port
        allowed_origins = [
            websockets.Origin(settings.plugin_origin),
            websockets.Origin("http://localhost:8787"),
            websockets.Origin("http://127.0.0.1:8787"),
            websockets.Origin("http://host.docker.internal:8787"),
            websockets.Origin("http://localhost:9001"),
            websockets.Origin("http://host.docker.internal:9001"),
        ]
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
            origins=allowed_origins,
            process_request=self._check_token,
            max_size=1_048_576,
        )

    async def stop(self) -> None:
        """Stop the standalone WebSocket server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("WebSocket Server stopped.")


# Singleton instance
ws_controller = PenpotWSController(host=settings.ws_host, port=settings.ws_port)
