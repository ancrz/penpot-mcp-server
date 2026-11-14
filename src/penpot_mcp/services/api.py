"""Penpot RPC API client for write operations and data retrieval."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from penpot_mcp.config import settings
from penpot_mcp.services.transit import decode_transit

logger = logging.getLogger(__name__)

_RPC_METHOD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_BLOCKED_INTERNAL_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "host.docker.internal",
        "gateway.docker.internal",
        "penpot-postgres",
        "penpot-redis",
        "penpot-backend",
        "penpot-frontend",
        "penpot-exporter",
        "penpot-mailcatch",
        "penpot-mcp",
    }
)


def _check_ip_ssrf(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> None:
    """Reject loopback, link-local (cloud metadata), unspecified, multicast, and private IPs."""
    if (
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_unspecified
        or ip_obj.is_multicast
    ):
        raise ValueError(f"SSRF guard blocked restricted IP address: {ip_obj}")
    if not allow_private and (ip_obj.is_private or ip_obj.is_reserved):
        raise ValueError(f"SSRF guard blocked private/reserved IP address: {ip_obj}")


def validate_rpc_method(method: str) -> str:
    """Validate Penpot RPC command name to prevent path traversal or query injection."""
    clean = (method or "").strip()
    if not _RPC_METHOD_RE.fullmatch(clean):
        raise ValueError(f"Invalid Penpot RPC command name: {method!r}")
    return clean


def validate_penpot_endpoint_url(url: str, *, field_name: str = "url") -> str:
    """Validate configured Penpot base/public endpoint URLs."""
    raw = (url or "").strip()
    if not raw or any( ord(c) < 32 for c in raw ):
        raise ValueError(f"Invalid {field_name}: empty or contains control characters")
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid {field_name} scheme {parsed.scheme!r}: only http/https allowed")
    if not parsed.hostname:
        raise ValueError(f"Invalid {field_name}: missing hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"Invalid {field_name}: embedded credentials are not allowed")
    return raw.rstrip("/")


def validate_external_media_url(
    url: str,
    *,
    allow_private: bool | None = None,
    resolve_dns: bool = False,
) -> str:
    """Validate external media URL against Server-Side Request Forgery (SSRF)."""
    eff_allow_private = (
        settings.ssrf_allow_private_media if allow_private is None else allow_private
    )
    raw = (url or "").strip()
    if not raw or any(ord(c) <= 32 for c in raw):
        raise ValueError("Invalid media URL: empty or contains whitespace/control characters")

    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"SSRF guard blocked scheme {parsed.scheme!r}: only http and https are allowed"
        )
    if not parsed.hostname:
        raise ValueError("Invalid media URL: missing hostname")
    if parsed.username or parsed.password:
        raise ValueError("SSRF guard blocked URL with embedded credentials")
    if parsed.fragment:
        raise ValueError("Invalid media URL: URL fragments are not permitted")

    host = parsed.hostname.lower().rstrip(".")
    if (
        host in _BLOCKED_INTERNAL_HOSTS
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError(f"SSRF guard blocked internal hostname: {host}")

    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        _check_ip_ssrf(ip_obj, allow_private=eff_allow_private)
    elif resolve_dns:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve media URL hostname {host!r}: {exc}") from exc
        for info in infos:
            resolved_ip = ipaddress.ip_address(info[4][0])
            _check_ip_ssrf(resolved_ip, allow_private=eff_allow_private)

    return raw


def validate_export_redirect_uri(uri: str, base_url: str, public_url: str) -> str:
    """Validate and normalize an exporter asset URI so it only targets the configured Penpot origin."""
    raw = (uri or "").strip()
    if not raw or any(ord(c) <= 32 for c in raw):
        raise ValueError("Invalid export URI")

    if raw.startswith("/"):
        if raw.startswith("//") or ".." in raw.split("/"):
            raise ValueError(f"SSRF guard blocked unsafe relative export URI: {raw!r}")
        return raw

    parsed_uri = urlsplit(raw)
    if parsed_uri.scheme not in ("http", "https") or not parsed_uri.netloc:
        raise ValueError(f"SSRF guard blocked non-HTTP export URI: {raw!r}")
    if parsed_uri.username or parsed_uri.password:
        raise ValueError("SSRF guard blocked export URI with embedded credentials")
    if ".." in parsed_uri.path.split("/"):
        raise ValueError("SSRF guard blocked path traversal in export URI")

    base_parsed = urlsplit(validate_penpot_endpoint_url(base_url, field_name="penpot_base_url"))
    pub_parsed = (
        urlsplit(validate_penpot_endpoint_url(public_url, field_name="penpot_public_url"))
        if public_url
        else None
    )

    uri_origin = (parsed_uri.scheme.lower(), parsed_uri.netloc.lower())
    base_origin = (base_parsed.scheme.lower(), base_parsed.netloc.lower())
    pub_origin = (
        (pub_parsed.scheme.lower(), pub_parsed.netloc.lower()) if pub_parsed else None
    )

    if uri_origin == pub_origin:
        return urlunsplit(
            (
                base_parsed.scheme,
                base_parsed.netloc,
                parsed_uri.path,
                parsed_uri.query,
                "",
            )
        )
    if uri_origin == base_origin:
        return urlunsplit(
            (
                base_parsed.scheme,
                base_parsed.netloc,
                parsed_uri.path,
                parsed_uri.query,
                "",
            )
        )

    raise ValueError(
        f"SSRF guard blocked untrusted export redirect origin: {parsed_uri.scheme}://{parsed_uri.netloc}"
    )

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_UUID_VALUE_KEYS = frozenset(
    {
        "id",
        "file-id",
        "frame-id",
        "main-instance-id",
        "main-instance-page",
        "object-id",
        "page-id",
        "parent-id",
        "session-id",
    }
)


def _is_uuid_value(key: str | None, parent: dict | None) -> bool:
    if not key:
        return False
    if key in _UUID_VALUE_KEYS or key.endswith(("-ids", "-id", "-file")):
        return True
    return bool(key == "val" and parent and str(parent.get("attr", "")).endswith("-id"))


def _escape_transit_string(value: str) -> str:
    if value.startswith(("~", "^")):
        return f"~{value}"
    return value


_KEYWORDS_TYPES = {
    "add-obj",
    "mod-obj",
    "del-obj",
    "add-page",
    "del-page",
    "mod-page",
    "mov-objects",
    "set",
    "add-color",
    "mod-color",
    "del-color",
    "add-typography",
    "mod-typography",
    "del-typography",
    "add-component",
}

_ENUM_VALUE_KEYS = {
    "stroke-style",
    "stroke-alignment",
    "layout",
    "layout-flex-dir",
    "layout-align-items",
    "layout-justify-content",
    "blend-mode",
    "constraints-h",
    "constraints-v",
}


def _encode_transit_value(
    value: Any,
    *,
    key: str | None = None,
    parent: dict | None = None,
) -> Any:
    """Encode the small Transit subset needed by Penpot change operations.

    JSON requests coerce top-level RPC UUID parameters, but UUIDs embedded in
    a shape modification remain strings and fail Penpot's shape schema. The
    update-file endpoint also accepts Transit JSON, where UUIDs and keywords
    retain their types.
    """
    if isinstance(value, dict):
        return {
            f"~:{str(item_key)}": _encode_transit_value(
                item_value,
                key=str(item_key),
                parent=value,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_encode_transit_value(item, key=key, parent=parent) for item in value]
    if isinstance(value, str):
        if _UUID_RE.fullmatch(value) and _is_uuid_value(key, parent):
            return f"~u{value}"
        if key == "type" and value in _KEYWORDS_TYPES:
            return f"~:{value}"
        if key == "attr" or key in _ENUM_VALUE_KEYS:
            return f"~:{value}"
        return _escape_transit_string(value)
    return value


class PenpotAPI:
    """Async HTTP client for Penpot's RPC API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._session_token: str | None = None

    async def connect(self) -> None:
        validated_base_url = validate_penpot_endpoint_url(
            settings.penpot_base_url, field_name="penpot_base_url"
        )
        if settings.penpot_public_url:
            validate_penpot_endpoint_url(
                settings.penpot_public_url, field_name="penpot_public_url"
            )
        self._client = httpx.AsyncClient(
            base_url=validated_base_url,
            timeout=30.0,
            follow_redirects=False,
        )
        if settings.has_access_token:
            self._client.headers["Authorization"] = (
                f"Token {settings.penpot_access_token}"
            )
            logger.info("Authenticated via access token")
            # Also login to obtain auth-token cookie for the exporter service,
            # which only accepts cookie auth, not Authorization headers.
            if settings.has_credentials:
                await self._login()
                logger.info("Also obtained session cookie for exporter auth")
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
        logger.info("Session established for account %s", settings.penpot_email)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Execute an RPC command against Penpot backend."""
        assert self._client is not None, "API client not connected"
        safe_method = validate_rpc_method(method)
        url = f"/api/rpc/command/{safe_method}"
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

    async def update_file_transit(
        self,
        file_id: str,
        session_id: str,
        revn: int,
        vern: int,
        changes: list[dict],
        features: list[str] | None = None,
    ) -> Any:
        """Submit typed Transit changes when nested UUID values are required."""
        assert self._client is not None, "API client not connected"
        params: dict[str, Any] = {
            "id": file_id,
            "session-id": session_id,
            "revn": revn,
            "vern": vern,
            "changes": changes,
        }
        if features:
            params["features"] = features
        body = json.dumps(_encode_transit_value(params))
        response = await self._client.post(
            "/api/rpc/command/update-file",
            content=body,
            headers={
                "Content-Type": "application/transit+json",
                "Accept": "application/transit+json",
            },
        )
        if response.is_error:
            raise RuntimeError(
                f"Penpot Transit update failed ({response.status_code}): "
                f"{response.text[:2000]}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/transit+json" in content_type:
            return decode_transit(response.text)
        if "application/json" in content_type:
            return response.json()
        return response.text

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
        safe_url = validate_external_media_url(url)
        return await self.command(
            "create-file-media-object-from-url",
            {"file-id": file_id, "name": name, "url": safe_url},
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

        if not auth_token and settings.has_credentials:
            logger.info("No auth-token cookie found; logging in for exporter auth")
            await self._login()
            for cookie in self._client.cookies.jar:
                if cookie.name == "auth-token":
                    auth_token = cookie.value
                    break

        if not auth_token:
            raise RuntimeError(
                "No auth-token cookie available for the exporter. "
                "Configure PENPOT_EMAIL and PENPOT_PASSWORD alongside "
                "PENPOT_ACCESS_TOKEN to enable exports."
            )

        headers = {"Content-Type": "application/transit+json"}
        cookies = {"auth-token": auth_token}

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
                safe_uri = validate_export_redirect_uri(
                    str(uri),
                    settings.penpot_base_url,
                    settings.penpot_public_url,
                )
                file_resp = await self._client.get(safe_uri, timeout=60.0)
                file_resp.raise_for_status()
                return file_resp.content
            return resp.content
        return resp.content

    # ── Access Tokens ────────────────────────────────────────

    async def create_access_token(self, name: str) -> dict:
        return await self.command("create-access-token", {"name": name})


# Singleton
api = PenpotAPI()
