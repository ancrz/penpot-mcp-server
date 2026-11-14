"""Sandboxed FFmpeg & Media Manipulation MCP Server (AEP Code Mode enabled).

Runs in an isolated container (`penpot-ffmpeg-mcp`) sharing `/sandbox` with
`penpot-mcp`. Exposes a single compressed AEP Code Mode tool (`ffmpeg_code_mode`)
over Streamable HTTP (`/mcp`) and `/api/call` to minimize token usage and
support multi-step `$steps.<id>.output_path` media pipelines in 1 turn.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

try:
    from penpot_mcp.code_mode import compact_payload, execute_code_pipeline
except ImportError:  # Standalone container layout (/app/code_mode.py)
    from code_mode import compact_payload, execute_code_pipeline  # type: ignore[import-not-found,no-redef]

SANDBOX_ROOT = Path(os.environ.get("SANDBOX_ROOT", "/sandbox")).resolve()
FFMPEG_TIMEOUT_SEC = float(os.environ.get("FFMPEG_TIMEOUT_SEC", "30"))
ALLOWED_IMAGE_FORMATS = frozenset({"png", "jpg", "jpeg", "webp", "avif", "gif", "bmp"})

mcp = FastMCP("penpot-ffmpeg-mcp")


def resolve_sandbox_path(rel_path: str) -> Path:
    """Resolve a path strictly inside SANDBOX_ROOT, blocking traversal."""
    cleaned = (rel_path or "").strip().lstrip("/")
    if not cleaned or "\\" in cleaned:
        raise ValueError(f"Ruta inválida en sandbox: {rel_path!r}")
    candidate = (SANDBOX_ROOT / cleaned).resolve()
    if candidate != SANDBOX_ROOT and SANDBOX_ROOT not in candidate.parents:
        raise ValueError(f"Path traversal bloqueado fuera de {SANDBOX_ROOT}: {rel_path!r}")
    return candidate


async def _run_exec(cmd: list[str]) -> tuple[int, str, str]:
    """Execute ffmpeg/ffprobe without shell interpolation."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT_SEC
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"FFmpeg timeout after {FFMPEG_TIMEOUT_SEC}s")
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def probe_media(input_path: str) -> dict[str, Any]:
    """Inspect image/video dimensions, format, and streams via ffprobe."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    code, out, err = await _run_exec(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(src),
        ]
    )
    if code != 0:
        raise RuntimeError(f"ffprobe error ({code}): {err[:300]}")
    data = json.loads(out or "{}")
    streams: list[dict[str, Any]] = data.get("streams") or []
    vstream: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
    fmt = data.get("format") or {}
    return {
        "status": "OK",
        "path": str(src.relative_to(SANDBOX_ROOT)),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "codec": vstream.get("codec_name"),
        "format": fmt.get("format_name"),
        "duration_sec": fmt.get("duration"),
        "size_bytes": src.stat().st_size,
    }


async def resize_image(
    input_path: str,
    output_name: str,
    width: int,
    height: int = -1,
) -> dict[str, Any]:
    """Resize an image inside /sandbox (use height=-1 to preserve aspect ratio)."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    dst = resolve_sandbox_path(f"outputs/{Path(output_name).name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    w = max(1, min(int(width), 8192))
    h = -1 if int(height) <= 0 else max(1, min(int(height), 8192))
    code, _, err = await _run_exec(
        ["ffmpeg", "-y", "-i", str(src), "-vf", f"scale={w}:{h}", str(dst)]
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg resize error ({code}): {err[:300]}")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "size_bytes": dst.stat().st_size,
        "width": w,
        "height": h,
    }


async def crop_image(
    input_path: str,
    output_name: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Crop an image region (w:h:x:y) inside /sandbox."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    dst = resolve_sandbox_path(f"outputs/{Path(output_name).name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    w = max(1, int(width))
    h = max(1, int(height))
    cx = max(0, int(x))
    cy = max(0, int(y))
    code, _, err = await _run_exec(
        ["ffmpeg", "-y", "-i", str(src), "-vf", f"crop={w}:{h}:{cx}:{cy}", str(dst)]
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg crop error ({code}): {err[:300]}")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "size_bytes": dst.stat().st_size,
    }


async def convert_image(
    input_path: str,
    output_name: str,
    target_format: str = "webp",
    quality: int = 85,
) -> dict[str, Any]:
    """Convert and compress an image to png/jpg/webp/avif inside /sandbox."""
    fmt = target_format.strip().lower().lstrip(".")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValueError(f"Formato no permitido: {target_format}")
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    stem = Path(output_name).stem or src.stem
    dst = resolve_sandbox_path(f"outputs/{stem}.{fmt}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    q = max(1, min(int(quality), 100))
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if fmt in {"webp", "avif"}:
        cmd.extend(["-quality", str(q)])
    elif fmt in {"jpg", "jpeg"}:
        qscale = max(1, min(31, round((100 - q) * 30 / 100) + 1))
        cmd.extend(["-q:v", str(qscale)])
    cmd.append(str(dst))
    code, _, err = await _run_exec(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg convert error ({code}): {err[:300]}")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "format": fmt,
        "size_bytes": dst.stat().st_size,
    }


async def extract_video_frame(
    input_path: str,
    output_name: str = "frame.png",
    timestamp: str = "00:00:01",
) -> dict[str, Any]:
    """Extract a single PNG frame from a video/GIF for visual inspection."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    dst = resolve_sandbox_path(f"outputs/{Path(output_name).stem}.png")
    dst.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = await _run_exec(
        ["ffmpeg", "-y", "-ss", timestamp, "-i", str(src), "-frames:v", "1", str(dst)]
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg frame extract error ({code}): {err[:300]}")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "size_bytes": dst.stat().st_size,
    }


async def dispatch_media_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a single FFmpeg action inside /sandbox."""
    act = (action or "").strip().lower()
    if act == "probe":
        return await probe_media(input_path=str(params.get("input_path") or ""))
    if act == "resize":
        return await resize_image(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "resized.png"),
            width=int(params.get("width") or 1024),
            height=int(params.get("height") or -1),
        )
    if act == "crop":
        return await crop_image(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "cropped.png"),
            x=int(params.get("x") or 0),
            y=int(params.get("y") or 0),
            width=int(params.get("width") or 512),
            height=int(params.get("height") or 512),
        )
    if act == "convert":
        return await convert_image(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "converted.webp"),
            target_format=str(params.get("format") or "webp"),
            quality=int(params.get("quality") or 85),
        )
    if act == "extract_frame":
        return await extract_video_frame(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "frame.png"),
            timestamp=str(params.get("timestamp") or "00:00:01"),
        )
    raise ValueError(f"Acción FFmpeg no soportada: {action}")


@mcp.tool()
async def ffmpeg_code_mode(
    mode: str = "call",
    action: str = "probe",
    params: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    vars: dict[str, Any] | None = None,
    verbosity: str = "summary",
    return_only: str = "summary",
    select_fields: list[str] | None = None,
) -> dict[str, Any]:
    """AEP Code Mode entry point for sandboxed FFmpeg media manipulation.

    Modes:
    - 'discover': list supported actions and $steps.<id>.output_path piping syntax.
    - 'call': run one action ('probe' | 'resize' | 'crop' | 'convert' | 'extract_frame').
    - 'pipeline': execute multi-step media DAG in 1 turn with compact token output.
    """
    m = (mode or "call").strip().lower()
    if m == "discover":
        return {
            "status": "OK",
            "service": "penpot-ffmpeg-mcp",
            "code_mode": True,
            "actions": ["probe", "resize", "crop", "convert", "extract_frame"],
            "piping": "$steps.<step_id>.output_path",
            "verbosity_levels": ["minimal", "summary", "full"],
        }
    if m == "pipeline":
        return await execute_code_pipeline(
            steps or [],
            dispatch_media_action,
            vars_dict=vars,
            return_only=return_only,
            select_fields=select_fields,
            verbosity=verbosity,
        )
    res = await dispatch_media_action(action, params or {})
    return compact_payload(
        res,
        verbosity=verbosity,
        return_only=return_only,
        select_fields=select_fields,
    )


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "penpot-ffmpeg-mcp",
            "code_mode": True,
            "sandbox": str(SANDBOX_ROOT),
        }
    )


async def _api_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        mode = str(body.get("mode") or ("pipeline" if body.get("steps") else "call"))
        res = await ffmpeg_code_mode(
            mode=mode,
            action=str(body.get("action") or "probe"),
            params=body.get("params") or {},
            steps=body.get("steps"),
            vars=body.get("vars"),
            verbosity=str(body.get("verbosity") or "summary"),
            return_only=str(body.get("return_only") or "summary"),
            select_fields=body.get("select_fields"),
        )
        return JSONResponse(res)
    except Exception as exc:
        return JSONResponse({"status": "ERROR", "error": str(exc)}, status_code=400)


def create_app() -> Starlette:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
            Route("/api/call", _api_call, methods=["POST"]),
            Mount("/", app=mcp.streamable_http_app()),
        ]
    )


def main() -> None:
    host = os.environ.get("FFMPEG_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("FFMPEG_MCP_PORT", "8890"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
