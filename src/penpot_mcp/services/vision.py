"""pgvector Semantic & Spatial Canvas Vision Engine ('Ojos para la IA del Gateway').

Gives any LLM in the Penpot AI Gateway (Gemini, Claude, ChatGPT, DeepSeek, Ollama)
precise spatial and semantic awareness of what the user is doing on the Penpot canvas
without requiring external browser screenshots.

Architecture:
1. Extracts shape hierarchy, geometry (x, y, width, height), colors, typography, and frame context.
2. Encodes each shape and user query into a 64-dimensional normalized vector (`vector(64)`)
   combining:
   - UI/UX role & shape type features (dims 0..7)
   - Spatial layout & bounding-box features (dims 8..15)
   - Chromatic RGB & luminance features (dims 16..21)
   - Multilingual (ES/EN) design token & character trigram hashes (dims 22..63)
3. Indexes shapes in PostgreSQL `public.mcp_canvas_vision_index` using `pgvector` HNSW cosine index
   and `pg_trgm`, and computes collision-free placement coordinates (`recommended_free_origin`).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

from penpot_mcp.services.db import db

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 64

# Multilingual (ES/EN) UI/UX semantic buckets mapped to dims 22..45
_SEMANTIC_BUCKETS: list[tuple[str, ...]] = [
    ("hero", "banner", "portada", "principal", "header", "encabezado"),
    ("card", "tarjeta", "panel", "box", "contenedor", "container"),
    ("button", "boton", "botón", "cta", "action", "accion", "submit", "enviar", "click"),
    ("title", "titulo", "título", "heading", "h1", "h2", "headline"),
    ("subtitle", "subtitulo", "subtítulo", "description", "descripcion", "body", "texto", "paragraph"),
    ("login", "signin", "auth", "sesion", "sesión", "password", "contraseña", "email", "correo", "form", "formulario"),
    ("input", "field", "campo", "textbox", "search", "buscar"),
    ("pricing", "precio", "precios", "plan", "planes", "pro", "subscription", "suscripcion", "mensual", "anual"),
    ("navbar", "nav", "menu", "menú", "navigation", "navegacion", "topbar", "sidebar", "footer"),
    ("badge", "pill", "tag", "etiqueta", "chip", "status", "estado"),
    ("icon", "icono", "avatar", "logo", "image", "imagen", "foto", "svg", "vector"),
    ("modal", "dialog", "popup", "drawer", "ventana", "overlay"),
    ("table", "tabla", "list", "lista", "grid", "fila", "row", "column", "columna"),
    ("chart", "grafico", "gráfico", "metric", "kpi", "dashboard", "analytics", "estadistica"),
    ("dark", "oscuro", "night", "negro", "slate", "navy", "#0", "#1", "#2"),
    ("light", "claro", "white", "blanco", "#f", "#e"),
    ("purple", "morado", "violeta", "indigo", "#6366f1", "#4f46e5", "#8b5cf6"),
    ("blue", "azul", "cyan", "celeste", "#0ea5e9", "#3b82f6", "#2563eb"),
    ("green", "verde", "emerald", "success", "exito", "#10b981", "#22c55e"),
    ("red", "rojo", "danger", "error", "alerta", "pink", "rosa", "#ef4444", "#ec4899"),
    ("yellow", "amarillo", "orange", "naranja", "warning", "gold", "#f59e0b", "#eab308"),
    ("top", "arriba", "superior", "header", "norte"),
    ("bottom", "abajo", "inferior", "footer", "sur"),
    ("left", "izquierda", "right", "derecha", "center", "centro", "medio"),
]


def _parse_hex_rgb(hex_color: str) -> tuple[float, float, float]:
    """Parse #RRGGBB or #RGB into normalized (r, g, b) in [0.0, 1.0]."""
    clean = (hex_color or "").strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return (0.5, 0.5, 0.5)
    try:
        r = int(clean[0:2], 16) / 255.0
        g = int(clean[2:4], 16) / 255.0
        b = int(clean[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return (0.5, 0.5, 0.5)


def _normalize_vector(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-9:
        vec[0] = 1.0
        return vec
    return [round(v / norm, 6) for v in vec]


def format_pgvector(vec: list[float]) -> str:
    """Serialize a float list into pgvector literal '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def extract_shape_fill(shape: dict[str, Any]) -> str:
    """Extract primary hex fill color from a Penpot shape dict."""
    if shape.get("fill_color"):
        return str(shape["fill_color"])
    fills = shape.get("fills")
    if isinstance(fills, list) and fills:
        first = fills[0]
        if isinstance(first, dict):
            return str(first.get("fillColor") or first.get("fill-color") or first.get("color") or "")
    return ""


def extract_shape_text(shape: dict[str, Any]) -> str:
    """Extract plain text content from a Penpot text shape or content tree."""
    if shape.get("characters"):
        return str(shape["characters"]).strip()
    if shape.get("text"):
        return str(shape["text"]).strip()
    content = shape.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        texts: list[str] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("text"), str):
                    texts.append(node["text"])
                for child in node.get("children") or []:
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(content)
        return " ".join(t.strip() for t in texts if t.strip())
    return ""


def build_semantic_summary(shape: dict[str, Any], frame_name: str = "") -> str:
    """Construct a rich human- and LLM-readable semantic + spatial description of a shape."""
    stype = str(shape.get("type") or "rect").lower()
    name = str(shape.get("name") or stype)
    x = round(float(shape.get("x") or 0), 1)
    y = round(float(shape.get("y") or 0), 1)
    w = round(float(shape.get("width") or 0), 1)
    h = round(float(shape.get("height") or 0), 1)
    fill = extract_shape_fill(shape)
    txt = extract_shape_text(shape)
    parts = [f"[{stype.upper()} '{name}']"]
    if frame_name and frame_name != name:
        parts.append(f"in frame '{frame_name}'")
    parts.append(f"at ({x}, {y}) size {w}x{h}")
    if fill:
        parts.append(f"fill={fill}")
    if txt:
        parts.append(f'text="{txt[:120]}"')
    return " ".join(parts)


def compute_shape_embedding(shape: dict[str, Any], frame_name: str = "") -> list[float]:
    """Encode a Penpot shape into a normalized 64-D vector for pgvector cosine similarity."""
    vec = [0.0] * EMBEDDING_DIM
    stype = str(shape.get("type") or "rect").lower()
    name = str(shape.get("name") or "").lower()
    txt = extract_shape_text(shape).lower()
    fill = extract_shape_fill(shape).lower()
    combined_text = f"{stype} {name} {frame_name.lower()} {txt} {fill}".strip()

    # Dims 0..7: Structural UI type & role
    vec[0] = 1.0 if stype == "frame" else 0.0
    vec[1] = 1.0 if stype == "text" else 0.0
    vec[2] = 1.0 if stype == "rect" else 0.0
    vec[3] = 1.0 if stype in ("circle", "ellipse", "path", "svg-raw", "image") else 0.0
    vec[4] = 1.0 if any(k in combined_text for k in ("button", "boton", "botón", "cta")) else 0.0
    vec[5] = 1.0 if any(k in combined_text for k in ("card", "hero", "container", "panel")) else 0.0
    vec[6] = 1.0 if any(k in combined_text for k in ("input", "form", "login", "field", "email")) else 0.0
    vec[7] = 1.0 if any(k in combined_text for k in ("nav", "navbar", "header", "menu", "pricing")) else 0.0

    # Dims 8..15: Normalized geometry
    x = float(shape.get("x") or 0.0)
    y = float(shape.get("y") or 0.0)
    w = max(1.0, float(shape.get("width") or 1.0))
    h = max(1.0, float(shape.get("height") or 1.0))
    vec[8] = max(-1.0, min(2.0, x / 1920.0))
    vec[9] = max(-1.0, min(2.0, y / 1080.0))
    vec[10] = min(2.0, w / 1920.0)
    vec[11] = min(2.0, h / 1080.0)
    vec[12] = min(2.0, w / max(1.0, h) / 4.0)
    vec[13] = min(2.0, (w * h) / (1920.0 * 1080.0))
    vec[14] = max(-1.0, min(2.0, (x + w * 0.5) / 1920.0))
    vec[15] = max(-1.0, min(2.0, (y + h * 0.5) / 1080.0))

    # Dims 16..21: Color & luminance
    if fill:
        r, g, b = _parse_hex_rgb(fill)
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        cmax, cmin = max(r, g, b), min(r, g, b)
        sat = (cmax - cmin) / cmax if cmax > 0 else 0.0
        vec[16], vec[17], vec[18] = r, g, b
        vec[19] = lum
        vec[20] = sat
        vec[21] = 1.0
    else:
        vec[21] = 0.0

    # Dims 22..45: Multilingual UI/UX semantic buckets
    for idx, bucket in enumerate(_SEMANTIC_BUCKETS):
        dim_idx = 22 + idx
        if dim_idx < 46 and any(tok in combined_text for tok in bucket):
            vec[dim_idx] = 1.25

    # Dims 46..63: Character trigram & token feature hashing
    tokens = re.findall(r"[a-z0-9áéíóúñ#]+", combined_text)
    for tok in tokens:
        h_val = int(hashlib.md5(tok.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16)
        bucket_idx = 46 + (h_val % (EMBEDDING_DIM - 46))
        sign = 1.0 if (h_val & 1) == 0 else -1.0
        vec[bucket_idx] += sign * 0.65
        if len(tok) >= 3:
            for i in range(len(tok) - 2):
                tri = tok[i : i + 3]
                th = int(hashlib.md5(tri.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16)
                t_idx = 46 + (th % (EMBEDDING_DIM - 46))
                vec[t_idx] += 0.25

    return _normalize_vector(vec)


def compute_query_embedding(query_text: str) -> list[float]:
    """Encode a natural-language user prompt or search query into the same 64-D vector space."""
    q = (query_text or "").strip().lower()
    synthetic_shape: dict[str, Any] = {
        "type": (
            "frame"
            if any(w in q for w in ("frame", "card", "tarjeta", "contenedor", "hero", "form", "login", "navbar"))
            else "text"
            if any(w in q for w in ("text", "texto", "titulo", "título", "heading", "label", "dice"))
            else "rect"
        ),
        "name": q,
        "text": q,
        "x": 960.0 if "derecha" in q or "right" in q else 120.0,
        "y": 600.0 if "abajo" in q or "bottom" in q else 120.0,
        "width": 480.0,
        "height": 280.0,
    }
    if any(w in q for w in ("morado", "indigo", "purple", "violeta")):
        synthetic_shape["fill_color"] = "#6366f1"
    elif any(w in q for w in ("azul", "blue", "celeste", "cyan")):
        synthetic_shape["fill_color"] = "#0ea5e9"
    elif any(w in q for w in ("oscuro", "dark", "negro", "slate")):
        synthetic_shape["fill_color"] = "#1e293b"
    elif any(w in q for w in ("verde", "green", "emerald")):
        synthetic_shape["fill_color"] = "#10b981"
    elif any(w in q for w in ("rojo", "red", "rosa", "pink")):
        synthetic_shape["fill_color"] = "#ef4444"
    return compute_shape_embedding(synthetic_shape)


async def sync_and_query_canvas_vision(
    file_id: str | None,
    page_id: str | None,
    query_text: str = "",
    top_k: int = 10,
) -> dict[str, Any]:
    """Index live Penpot page objects into PostgreSQL `pgvector` and return semantic + spatial vision context."""
    if not file_id or not page_id:
        return {
            "enabled": False,
            "engine": "pgvector-0.8.7",
            "total_shapes": 0,
            "matched_shapes": [],
            "recommended_free_origin": {"x": 80.0, "y": 80.0},
            "prompt_eye_summary": "No active file_id/page_id resolved yet.",
        }

    from penpot_mcp.gateway import gateway
    from penpot_mcp.services.api import api
    from penpot_mcp.tools.shapes import _get_page_objects

    raw_shapes: list[dict[str, Any]] = []
    try:
        page_res = await gateway.execute_intent(
            "get_page_objects", file_id=file_id, page_id=page_id
        )
        if isinstance(page_res, list):
            items: Any = page_res
        elif isinstance(page_res, dict):
            items = page_res.get("objects") or page_res.get("items") or []
            if isinstance(items, dict):
                items = list(items.values())
        else:
            items = []

        # Single-pass full shape enrichment from file_data if items only contain brief properties
        full_decoded: dict[str, dict[str, Any]] = {}
        if items and all(not isinstance(i, dict) or ("fills" not in i and "fill_color" not in i) for i in items):
            try:
                file_data = await api.get_file(file_id)
                if isinstance(file_data, dict):
                    full_decoded = _get_page_objects(file_data, page_id)
            except Exception:
                full_decoded = {}

        for item in items or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "")
            if not sid or sid == "00000000-0000-0000-0000-000000000000":
                continue
            if sid in full_decoded:
                d_shape = full_decoded[sid]
                item = {
                    **d_shape,
                    **item,
                    "parent_id": item.get("parent_id") or d_shape.get("parent-id") or d_shape.get("frame-id"),
                    "fills": item.get("fills") or d_shape.get("fills") or [],
                    "content": item.get("content") or d_shape.get("content"),
                }
            raw_shapes.append(item)
    except Exception as exc:
        logger.debug("Vision shape extraction warning: %s", exc)

    # Map frame IDs to frame names for hierarchy context
    frame_lookup: dict[str, str] = {}
    for s in raw_shapes:
        if str(s.get("type") or "").lower() == "frame" and s.get("id"):
            frame_lookup[str(s["id"])] = str(s.get("name") or "Frame")

    prepared_rows: list[dict[str, Any]] = []
    max_right = 0.0
    min_top = 80.0
    frames_summary: list[dict[str, Any]] = []

    for s in raw_shapes:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        stype = str(s.get("type") or "rect").lower()
        sname = str(s.get("name") or stype)
        parent_id = str(s.get("parent_id") or s.get("parentId") or s.get("frame_id") or "")
        frame_name = frame_lookup.get(parent_id, sname if stype == "frame" else "")
        x = float(s.get("x") or 0.0)
        y = float(s.get("y") or 0.0)
        w = float(s.get("width") or 0.0)
        h = float(s.get("height") or 0.0)
        fill = extract_shape_fill(s)
        txt = extract_shape_text(s)
        summary = build_semantic_summary(s, frame_name=frame_name)
        emb = compute_shape_embedding(s, frame_name=frame_name)

        if x + w > max_right:
            max_right = x + w
        if stype == "frame":
            frames_summary.append(
                {
                    "id": sid,
                    "name": sname,
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "width": round(w, 1),
                    "height": round(h, 1),
                    "fill_color": fill,
                }
            )

        prepared_rows.append(
            {
                "shape_id": sid,
                "shape_name": sname,
                "shape_type": stype,
                "parent_id": parent_id if parent_id and parent_id != "00000000-0000-0000-0000-000000000000" else None,
                "frame_name": frame_name,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "fill_color": fill,
                "text_content": txt,
                "semantic_summary": summary,
                "embedding": format_pgvector(emb),
            }
        )

    # Calculate non-overlapping next free coordinates on the canvas
    if max_right > 0:
        free_x = round(max_right + 60.0, 1)
        free_y = round(min_top, 1)
        if free_x > 1800:
            free_x = 80.0
            max_bottom = max((r["y"] + r["height"] for r in prepared_rows), default=80.0)
            free_y = round(max_bottom + 60.0, 1)
    else:
        free_x, free_y = 80.0, 80.0

    # Sync to PostgreSQL pgvector index and run cosine similarity search
    q_emb = format_pgvector(compute_query_embedding(query_text or "hero card title button frame"))
    pg_matches: list[dict[str, Any]] = []
    pgvector_active = False

    if db._pool is not None:
        try:
            await db.index_page_vision_rows(file_id, page_id, prepared_rows)
            pg_matches = await db.search_page_vision_pgvector(
                file_id=file_id,
                page_id=page_id,
                query_vector_literal=q_emb,
                query_text=query_text,
                top_k=top_k,
            )
            pgvector_active = True
        except Exception as exc:
            logger.debug("pgvector query fallback to in-memory cosine: %s", exc)

    if not pg_matches and prepared_rows:
        # Fallback in-memory cosine ranking if DB pool is not connected in unit test
        q_vec = compute_query_embedding(query_text or "hero card title button frame")
        scored: list[dict[str, Any]] = []
        for r in prepared_rows:
            s_vec = compute_shape_embedding(
                {
                    "type": r["shape_type"],
                    "name": r["shape_name"],
                    "x": r["x"],
                    "y": r["y"],
                    "width": r["width"],
                    "height": r["height"],
                    "fill_color": r["fill_color"],
                    "text": r["text_content"],
                },
                frame_name=r["frame_name"],
            )
            sim = round(sum(a * b for a, b in zip(q_vec, s_vec)), 4)
            scored.append(
                {
                    "shape_id": r["shape_id"],
                    "shape_name": r["shape_name"],
                    "shape_type": r["shape_type"],
                    "frame_name": r["frame_name"],
                    "x": round(r["x"], 1),
                    "y": round(r["y"], 1),
                    "width": round(r["width"], 1),
                    "height": round(r["height"], 1),
                    "fill_color": r["fill_color"],
                    "text_content": r["text_content"],
                    "semantic_summary": r["semantic_summary"],
                    "similarity": sim,
                }
            )
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        pg_matches = scored[:top_k]

    # Build concise visual eye summary for the LLM system prompt
    if pg_matches:
        lines = [
            f"Canvas Vision Eye (pgvector={'ACTIVE' if pgvector_active else 'MEM-FALLBACK'}, "
            f"total_shapes={len(prepared_rows)}, frames={len(frames_summary)}, "
            f"recommended_free_origin=({free_x}, {free_y})):"
        ]
        for idx, m in enumerate(pg_matches[:8], start=1):
            lines.append(
                f"  {idx}. {m['semantic_summary']} (id={m['shape_id']}, sim={m.get('similarity', 0):.3f})"
            )
        eye_summary = "\n".join(lines)
    else:
        eye_summary = (
            f"Canvas Vision Eye (pgvector={'ACTIVE' if pgvector_active else 'READY'}): "
            f"Canvas is currently empty (0 shapes). Recommended start coordinates: ({free_x}, {free_y})."
        )

    return {
        "enabled": True,
        "pgvector_active": pgvector_active,
        "engine": "pgvector-0.8.7 (vector(64) HNSW cosine + pg_trgm)",
        "file_id": file_id,
        "page_id": page_id,
        "total_shapes": len(prepared_rows),
        "frames": frames_summary,
        "matched_shapes": pg_matches,
        "recommended_free_origin": {"x": free_x, "y": free_y},
        "prompt_eye_summary": eye_summary,
    }
