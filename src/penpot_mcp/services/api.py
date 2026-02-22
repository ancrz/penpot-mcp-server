"""Penpot RPC API client for write operations and data retrieval."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from penpot_mcp.config import settings
from penpot_mcp.services.transit import decode_transit

logger = logging.getLogger(__name__)


class PenpotAPI:
    """Async HTTP client for Penpot's RPC API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._session_token: str | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.penpot_base_url,
            timeout=30.0,
            follow_redirects=True,
        )
        if settings.has_access_token:
            self._client.headers["Authorization"] = (
                f"Token {settings.penpot_access_token}"
            )
            logger.info("Authenticated via access token")
        elif settings.has_credentials:
            await self._login()
        else:
            logger.warning("No authentication configured — API calls may fail")

    async def _login(self) -> None:
        assert self._client is not None, "API client not connected"
        resp = await self._client.post(
            "/api/rpc/command/login-with-password",
            json={"email": settings.penpot_email, "password": settings.penpot_password},
        )
        resp.raise_for_status()
        logger.info("Authenticated via credentials for %s", settings.penpot_email)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Execute an RPC command against Penpot backend."""
        assert self._client is not None, "API client not connected"
        url = f"/api/rpc/command/{method}"
        if params:
            resp = await self._client.post(url, json=params)
        else:
            resp = await self._client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        if "application/transit+json" in content_type:
            return decode_transit(resp.text)
        return resp.text

    # ── Profile ──────────────────────────────────────────────

    async def get_profile(self) -> dict:
        return await self.command("get-profile")

    # ── Projects ─────────────────────────────────────────────

    async def get_projects(self, team_id: str) -> list[dict]:
        return await self.command("get-projects", {"team-id": team_id})

    async def create_project(self, team_id: str, name: str) -> dict:
        return await self.command("create-project", {"team-id": team_id, "name": name})

    async def rename_project(self, project_id: str, name: str) -> dict:
        return await self.command("rename-project", {"id": project_id, "name": name})

    # ── Files ────────────────────────────────────────────────

    async def get_file(self, file_id: str, features: list[str] | None = None) -> dict:
        params: dict[str, Any] = {"id": file_id}
        if features:
            params["features"] = features
        return await self.command("get-file", params)

    async def get_files(self, project_id: str) -> list[dict]:
        return await self.command("get-files", {"project-id": project_id})

    async def create_file(self, project_id: str, name: str) -> dict:
        return await self.command(
            "create-file", {"project-id": project_id, "name": name}
        )

    async def rename_file(self, file_id: str, name: str) -> dict:
        return await self.command("rename-file", {"id": file_id, "name": name})

    async def duplicate_file(self, file_id: str, name: str | None = None) -> dict:
        params: dict[str, Any] = {"file-id": file_id}
        if name:
            params["name"] = name
        return await self.command("duplicate-file", params)

    async def delete_file(self, file_id: str) -> dict:
        return await self.command("delete-file", {"id": file_id})

    # ── File Updates (Change Operations) ─────────────────────

    async def update_file(
        self,
        file_id: str,
        session_id: str,
        revn: int,
        vern: int,
        changes: list[dict],
        features: list[str] | None = None,
    ) -> dict:
        """Submit changes to a file using Penpot's change system."""
        params: dict[str, Any] = {
            "id": file_id,
            "session-id": session_id,
            "revn": revn,
            "vern": vern,
            "changes": changes,
        }
        if features:
            params["features"] = features
        return await self.command("update-file", params)

    # ── Comments ─────────────────────────────────────────────

    async def create_comment_thread(
        self,
        file_id: str,
        page_id: str,
        position: dict,
        content: str,
        frame_id: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "file-id": file_id,
            "page-id": page_id,
            "position": position,
            "content": content,
        }
        if frame_id:
            params["frame-id"] = frame_id
        return await self.command("create-comment-thread", params)

    async def create_comment(self, thread_id: str, content: str) -> dict:
        return await self.command(
            "create-comment", {"thread-id": thread_id, "content": content}
        )

    async def update_comment_thread(self, thread_id: str, is_resolved: bool) -> dict:
        return await self.command(
            "update-comment-thread", {"id": thread_id, "is-resolved": is_resolved}
        )

    # ── Media ────────────────────────────────────────────────

    async def upload_media(self, file_id: str, name: str, url: str) -> dict:
        return await self.command(
            "create-file-media-object-from-url",
            {"file-id": file_id, "name": name, "url": url},
        )

    # ── Snapshots ────────────────────────────────────────────

    async def create_snapshot(self, file_id: str, label: str) -> dict:
        return await self.command(
            "create-file-snapshot", {"file-id": file_id, "label": label}
        )

    async def get_snapshots(self, file_id: str) -> list[dict]:
        return await self.command("get-file-snapshots", {"file-id": file_id})

    # ── Export ─────────────────────────────────────────────────

    async def export_object(
        self,
        file_id: str,
        page_id: str,
        object_id: str,
        export_type: str = "png",
        scale: float = 1.0,
        name: str = "export",
    ) -> bytes:
        """Export a frame/shape via the Penpot exporter service.

        The exporter expects Transit JSON-Verbose format with cookie auth.
        Uses the export-shapes command with wait=true for synchronous response.

        Returns raw bytes of the rendered content.
        """
        # Get profile ID for the export request
        profile = await self.get_profile()
        profile_id = profile.get("id")

        # Build Transit JSON-Verbose payload
        transit_body = json.dumps(
            {
                "~:cmd": "~:export-shapes",
                "~:profile-id": f"~u{profile_id}",
                "~:wait": True,
                "~:exports": [
                    {
                        "~:page-id": f"~u{page_id}",
                        "~:file-id": f"~u{file_id}",
                        "~:object-id": f"~u{object_id}",
                        "~:type": f"~:{export_type}",
                        "~:suffix": "",
                        "~:scale": scale,
                        "~:name": name,
                    }
                ],
            }
        )

        # The exporter uses cookie auth — extract from client's cookie jar
        auth_token = None
        assert self._client is not None, "API client not connected"
        for cookie in self._client.cookies.jar:
            if cookie.name == "auth-token":
                auth_token = cookie.value
                break

        headers = {"Content-Type": "application/transit+json"}
        cookies = {"auth-token": auth_token} if auth_token else {}

        resp = await self._client.post(
            "/api/export",
            content=transit_body,
            headers=headers,
            cookies=cookies,
            timeout=60.0,
        )
        resp.raise_for_status()

        # Response may be transit+json with a URI, or direct binary
        content_type = resp.headers.get("content-type", "")
        if "application/transit+json" in content_type:
            result = decode_transit(resp.text)
            # Transit decoder resolves ~#uri to a plain string
            uri = None
            if isinstance(result, str) and result.startswith("http"):
                uri = result
            elif isinstance(result, dict):
                uri = result.get("uri") or result.get("~#uri")
            if uri:
                # Rewrite public URL to internal URL for Docker networking
                public_url = settings.penpot_public_url.rstrip("/")
                internal_url = settings.penpot_base_url.rstrip("/")
                if public_url and uri.startswith(public_url):
                    uri = uri.replace(public_url, internal_url, 1)
                file_resp = await self._client.get(uri, timeout=60.0)
                file_resp.raise_for_status()
                return file_resp.content
            return resp.content
        return resp.content

    # ── Access Tokens ────────────────────────────────────────

    async def create_access_token(self, name: str) -> dict:
        return await self.command("create-access-token", {"name": name})


# Singleton
api = PenpotAPI()
