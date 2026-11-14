"""Unified Design, Vector, Palette, Document & Data Suite MCP (AEP Code Mode).

Runs in an isolated container (`penpot-suite-mcp`) sharing `/sandbox` with
`penpot-mcp` and `penpot-ffmpeg-mcp`. Absorbs 5 standalone capabilities into a
single AEP Code Mode tool (`suite_code_mode`) to minimize token overhead:
  1. Vector / SVG: `svg_optimize`, `raster_to_svg`
  2. Palette & WCAG: `extract_palette`, `wcag_contrast`
  3. Background Removal: `remove_background`
  4. Documents & Spreadsheets: `doc_extract`, `pdf_render_pages`
  5. Bounded Data Sandbox: `data_sandbox_query`
"""

from __future__ import annotations

import asyncio
from collections import deque
import csv
import io
import json
import os
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

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
SUITE_TIMEOUT_SEC = float(os.environ.get("SUITE_TIMEOUT_SEC", "30"))

mcp = FastMCP("penpot-suite-mcp")


def resolve_sandbox_path(rel_path: str) -> Path:
    """Resolve a path strictly inside SANDBOX_ROOT, blocking traversal."""
    cleaned = (rel_path or "").strip().lstrip("/")
    if not cleaned or "\\" in cleaned:
        raise ValueError(f"Ruta inválida en sandbox: {rel_path!r}")
    candidate = (SANDBOX_ROOT / cleaned).resolve()
    if candidate != SANDBOX_ROOT and SANDBOX_ROOT not in candidate.parents:
        raise ValueError(f"Path traversal bloqueado fuera de {SANDBOX_ROOT}: {rel_path!r}")
    return candidate


def _parse_hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Color hex inválido: {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def _chan(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


async def wcag_contrast(fg_hex: str, bg_hex: str) -> dict[str, Any]:
    """Calculate WCAG 2.1 contrast ratio and AA/AAA pass flags."""
    l1 = _relative_luminance(_parse_hex_rgb(fg_hex))
    l2 = _relative_luminance(_parse_hex_rgb(bg_hex))
    lighter, darker = max(l1, l2), min(l1, l2)
    ratio = round((lighter + 0.05) / (darker + 0.05), 2)
    return {
        "status": "OK",
        "fg": fg_hex,
        "bg": bg_hex,
        "ratio": ratio,
        "aa_normal": ratio >= 4.5,
        "aa_large": ratio >= 3.0,
        "aaa_normal": ratio >= 7.0,
        "aaa_large": ratio >= 4.5,
    }


async def extract_palette(input_path: str, n_colors: int = 6) -> dict[str, Any]:
    """Extract dominant hex palette from an uploaded image inside /sandbox."""
    from PIL import Image  # type: ignore[import-not-found]

    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    k = max(2, min(int(n_colors), 16))
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        rgb.thumbnail((256, 256))
        quant = rgb.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette() or []
        counts = quant.getcolors(maxcolors=k * 2) or []
    total = sum(c for c, _ in counts) or 1
    colors: list[dict[str, Any]] = []
    for count, idx in sorted(counts, reverse=True)[:k]:
        base = idx * 3
        if base + 2 < len(palette):
            r, g, b = palette[base], palette[base + 1], palette[base + 2]
            colors.append(
                {
                    "hex": f"#{r:02X}{g:02X}{b:02X}",
                    "rgb": [r, g, b],
                    "pct": round(count * 100.0 / total, 1),
                }
            )
    return {"status": "OK", "path": input_path, "colors": colors}


async def svg_optimize(input_path: str, output_name: str = "optimized.svg") -> dict[str, Any]:
    """Strip editor metadata, comments, and empty groups from an SVG safely."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    raw = src.read_text(encoding="utf-8", errors="replace")
    # Block DOCTYPE / ENTITY declarations before XML parsing
    sanitized = re.sub(r"<!DOCTYPE[^>]*>|<!ENTITY[^>]*>", "", raw, flags=re.IGNORECASE)
    root = ET.fromstring(sanitized)
    drop_prefixes = (
        "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}",
        "{http://www.inkscape.org/namespaces/inkscape}",
    )

    def _clean(elem: ET.Element) -> None:
        for attr in list(elem.attrib):
            if any(attr.startswith(p) for p in drop_prefixes):
                del elem.attrib[attr]
        kept: list[ET.Element] = []
        for child in list(elem):
            tag_lower = child.tag.lower()
            if tag_lower.endswith("metadata") or any(child.tag.startswith(p) for p in drop_prefixes):
                continue
            _clean(child)
            if tag_lower.endswith("}g") or tag_lower == "g":
                if len(child) == 0 and not (child.text or "").strip():
                    continue
            kept.append(child)
        elem[:] = kept

    _clean(root)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    out_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    dst = resolve_sandbox_path(f"outputs/{Path(output_name).stem}.svg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out_bytes)
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "original_bytes": src.stat().st_size,
        "size_bytes": dst.stat().st_size,
    }


async def raster_to_svg(
    input_path: str,
    output_name: str = "vectorized.svg",
    max_colors: int = 6,
    max_dim: int = 96,
) -> dict[str, Any]:
    """Trace a raster icon/logo into a quantized horizontal-run SVG."""
    from PIL import Image  # type: ignore[import-not-found]

    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    dim = max(16, min(int(max_dim), 160))
    k = max(2, min(int(max_colors), 12))
    with Image.open(src) as im:
        rgba = im.convert("RGBA")
        rgba.thumbnail((dim, dim))
        w, h = rgba.size
        alpha = rgba.getchannel("A")
        quant = rgba.convert("RGB").quantize(colors=k, method=Image.Quantize.MEDIANCUT)
        pal = quant.getpalette() or []
        q_px = quant.load()
        a_px = alpha.load()

    rects: list[str] = []
    for y in range(h):
        x = 0
        while x < w:
            if a_px is None or q_px is None or a_px[x, y] < 32:
                x += 1
                continue
            cidx = int(q_px[x, y])
            run_start = x
            while x < w and a_px[x, y] >= 32 and int(q_px[x, y]) == cidx:
                x += 1
            base = cidx * 3
            if base + 2 < len(pal):
                hex_c = f"#{pal[base]:02X}{pal[base + 1]:02X}{pal[base + 2]:02X}"
                rects.append(
                    f'<rect x="{run_start}" y="{y}" width="{x - run_start}" height="1" fill="{hex_c}"/>'
                )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'shape-rendering="crispEdges">{"".join(rects)}</svg>'
    )
    dst = resolve_sandbox_path(f"outputs/{Path(output_name).stem}.svg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(svg, encoding="utf-8")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "width": w,
        "height": h,
        "rects_count": len(rects),
        "size_bytes": dst.stat().st_size,
    }


async def remove_background(
    input_path: str,
    output_name: str = "nobg.png",
    tolerance: int = 28,
) -> dict[str, Any]:
    """Remove contiguous border background using corner-seeded BFS alpha matting."""
    from PIL import Image  # type: ignore[import-not-found]

    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    tol_sq = max(4, min(int(tolerance), 120)) ** 2 * 3
    with Image.open(src) as im:
        rgba = im.convert("RGBA")
        w, h = rgba.size
        px = rgba.load()
        if px is None:
            raise RuntimeError("No se pudieron leer los píxeles de la imagen.")
        seed = px[0, 0][:3]
        visited = bytearray(w * h)
        queue: deque[tuple[int, int]] = deque([(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)])
        while queue:
            cx, cy = queue.popleft()
            if cx < 0 or cy < 0 or cx >= w or cy >= h:
                continue
            idx = cy * w + cx
            if visited[idx]:
                continue
            visited[idx] = 1
            r, g, b, _ = px[cx, cy]
            dist_sq = (r - seed[0]) ** 2 + (g - seed[1]) ** 2 + (b - seed[2]) ** 2
            if dist_sq <= tol_sq:
                px[cx, cy] = (r, g, b, 0)
                queue.append((cx + 1, cy))
                queue.append((cx - 1, cy))
                queue.append((cx, cy + 1))
                queue.append((cx, cy - 1))

        dst = resolve_sandbox_path(f"outputs/{Path(output_name).stem}.png")
        dst.parent.mkdir(parents=True, exist_ok=True)
        rgba.save(dst, format="PNG")
    return {
        "status": "OK",
        "output_path": str(dst.relative_to(SANDBOX_ROOT)),
        "width": w,
        "height": h,
        "size_bytes": dst.stat().st_size,
    }


async def doc_extract(
    input_path: str,
    max_chars: int = 8000,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Extract compact text/tables from PDF, DOCX, XLSX, CSV, or text files."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    ext = src.suffix.lower()
    cap = max(500, min(int(max_chars), 20000))
    text = ""
    meta: dict[str, Any] = {"ext": ext}

    if ext == ".pdf":
        from pypdf import PdfReader  # type: ignore[import-not-found]

        reader = PdfReader(str(src))
        meta["pages"] = len(reader.pages)
        chunks = []
        for i, page in enumerate(reader.pages[:20], start=1):
            chunks.append(f"## Página {i}\n{(page.extract_text() or '').strip()}")
        text = "\n\n".join(chunks)
    elif ext == ".docx":
        from docx import Document  # type: ignore[import-not-found]

        doc = Document(str(src))
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        table_lines: list[str] = []
        for t in doc.tables[:10]:
            for row in t.rows[:30]:
                table_lines.append(" | ".join(cell.text.strip().replace("\n", " ") for cell in row.cells))
        meta["paragraphs"] = len(paras)
        meta["tables"] = len(doc.tables)
        text = "\n".join(paras + (["", "### Tablas"] + table_lines if table_lines else []))
    elif ext == ".xlsx":
        from openpyxl import load_workbook  # type: ignore[import-untyped]

        wb = load_workbook(str(src), read_only=True, data_only=True)
        meta["sheets"] = wb.sheetnames
        target = sheet if sheet and sheet in wb.sheetnames else wb.sheetnames[0]
        ws = wb[target]
        lines = [f"### Hoja: {target}"]
        for idx, row in enumerate(ws.iter_rows(values_only=True)):
            if idx >= 60:
                break
            lines.append(" | ".join("" if v is None else str(v) for v in row))
        wb.close()
        text = "\n".join(lines)
    elif ext == ".csv":
        raw = src.read_text(encoding="utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(raw)))
        meta["rows"] = len(rows)
        text = "\n".join(" | ".join(r) for r in rows[:60])
    else:
        text = src.read_text(encoding="utf-8", errors="replace")

    return {
        "status": "OK",
        "path": input_path,
        "meta": meta,
        "content": text[:cap],
        "truncated": len(text) > cap,
    }


async def pdf_render_pages(
    input_path: str,
    prefix: str = "pdf_page",
    dpi: int = 144,
    max_pages: int = 4,
) -> dict[str, Any]:
    """Render PDF pages to PNG inside /sandbox/outputs/ using pdftoppm."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    out_dir = resolve_sandbox_path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", Path(prefix).name) or "pdf_page"
    target_prefix = out_dir / safe_prefix
    proc = await asyncio.create_subprocess_exec(
        "pdftoppm",
        "-png",
        "-r",
        str(max(72, min(int(dpi), 300))),
        "-f",
        "1",
        "-l",
        str(max(1, min(int(max_pages), 10))),
        str(src),
        str(target_prefix),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err_b = await asyncio.wait_for(proc.communicate(), timeout=SUITE_TIMEOUT_SEC)
    if (proc.returncode or 0) != 0:
        raise RuntimeError(f"pdftoppm error: {err_b.decode('utf-8', errors='replace')[:300]}")
    generated = sorted(
        str(p.relative_to(SANDBOX_ROOT))
        for p in out_dir.glob(f"{safe_prefix}*.png")
    )
    return {
        "status": "OK",
        "output_path": generated[0] if generated else None,
        "pages_rendered": generated,
        "count": len(generated),
    }


async def data_sandbox_query(
    input_path: str,
    filter_col: str = "",
    filter_contains: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Safely inspect/filter CSV or JSON datasets inside /sandbox (no eval)."""
    src = resolve_sandbox_path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"No existe en sandbox: {input_path}")
    cap = max(1, min(int(limit), 200))
    ext = src.suffix.lower()
    records: list[dict[str, Any]] = []
    if ext == ".csv":
        raw = src.read_text(encoding="utf-8", errors="replace")
        records = list(csv.DictReader(io.StringIO(raw)))
    elif ext == ".json":
        parsed = json.loads(src.read_text(encoding="utf-8", errors="replace"))
        if isinstance(parsed, list):
            records = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            records = [parsed]
    else:
        raise ValueError(f"data_sandbox_query solo soporta .csv o .json (recibido {ext})")

    if filter_col and filter_contains:
        needle = filter_contains.lower()
        records = [r for r in records if needle in str(r.get(filter_col, "")).lower()]

    columns = list(records[0].keys()) if records else []
    return {
        "status": "OK",
        "path": input_path,
        "total_matching": len(records),
        "columns": columns,
        "rows": records[:cap],
    }


async def dispatch_suite_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch any unified Design & Document Suite action."""
    act = (action or "").strip().lower()
    if act == "svg_optimize":
        return await svg_optimize(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "optimized.svg"),
        )
    if act == "raster_to_svg":
        return await raster_to_svg(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "vectorized.svg"),
            max_colors=int(params.get("max_colors") or 6),
            max_dim=int(params.get("max_dim") or 96),
        )
    if act == "extract_palette":
        return await extract_palette(
            input_path=str(params.get("input_path") or ""),
            n_colors=int(params.get("n_colors") or 6),
        )
    if act == "wcag_contrast":
        return await wcag_contrast(
            fg_hex=str(params.get("fg_hex") or "#FFFFFF"),
            bg_hex=str(params.get("bg_hex") or "#0F172A"),
        )
    if act == "remove_background":
        return await remove_background(
            input_path=str(params.get("input_path") or ""),
            output_name=str(params.get("output_name") or "nobg.png"),
            tolerance=int(params.get("tolerance") or 28),
        )
    if act == "doc_extract":
        return await doc_extract(
            input_path=str(params.get("input_path") or ""),
            max_chars=int(params.get("max_chars") or 8000),
            sheet=params.get("sheet"),
        )
    if act == "pdf_render_pages":
        return await pdf_render_pages(
            input_path=str(params.get("input_path") or ""),
            prefix=str(params.get("prefix") or "pdf_page"),
            dpi=int(params.get("dpi") or 144),
            max_pages=int(params.get("max_pages") or 4),
        )
    if act == "data_sandbox_query":
        return await data_sandbox_query(
            input_path=str(params.get("input_path") or ""),
            filter_col=str(params.get("filter_col") or ""),
            filter_contains=str(params.get("filter_contains") or ""),
            limit=int(params.get("limit") or 50),
        )
    raise ValueError(f"Acción de Suite MCP no soportada: {action}")


@mcp.tool()
async def suite_code_mode(
    mode: str = "call",
    action: str = "extract_palette",
    params: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    vars: dict[str, Any] | None = None,
    verbosity: str = "summary",
    return_only: str = "summary",
    select_fields: list[str] | None = None,
) -> dict[str, Any]:
    """AEP Code Mode entry point for Vector/SVG, Palette/WCAG, Docs, BgRemoval & Data.

    Modes:
    - 'discover': list all 8 suite actions and $steps.<id>.output_path piping syntax.
    - 'call': run one action with compact token output.
    - 'pipeline': execute multi-step DAG (e.g. remove_background -> raster_to_svg) in 1 turn.
    """
    m = (mode or "call").strip().lower()
    if m == "discover":
        return {
            "status": "OK",
            "service": "penpot-suite-mcp",
            "code_mode": True,
            "actions": [
                "svg_optimize",
                "raster_to_svg",
                "extract_palette",
                "wcag_contrast",
                "remove_background",
                "doc_extract",
                "pdf_render_pages",
                "data_sandbox_query",
            ],
            "piping": "$steps.<step_id>.output_path",
            "verbosity_levels": ["minimal", "summary", "full"],
        }
    if m == "pipeline":
        return await execute_code_pipeline(
            steps or [],
            dispatch_suite_action,
            vars_dict=vars,
            return_only=return_only,
            select_fields=select_fields,
            verbosity=verbosity,
        )
    res = await dispatch_suite_action(action, params or {})
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
            "service": "penpot-suite-mcp",
            "code_mode": True,
            "sandbox": str(SANDBOX_ROOT),
        }
    )


async def _api_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        mode = str(body.get("mode") or ("pipeline" if body.get("steps") else "call"))
        res = await suite_code_mode(
            mode=mode,
            action=str(body.get("action") or "extract_palette"),
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
    host = os.environ.get("SUITE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("SUITE_MCP_PORT", "8891"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
