"""Attachment & .penpot template parser for Penpot AI Studio chat context.

Safely parses uploaded files (`.penpot` ZIP archives, `.svg`, `.json`, `.md`,
`.txt`, `.css`, and reference images) into compact token-budgeted context
blocks for the AI Gateway, with Zip-slip and Zip-bomb protections.
"""

from __future__ import annotations

import base64
import io
import json
import os
import uuid
import zipfile
from typing import Any

MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # 16 MB
MAX_ZIP_ENTRIES = 250
MAX_ZIP_UNCOMPRESSED_BYTES = 24 * 1024 * 1024  # 24 MB
MAX_CONTEXT_CHARS = 8000

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_TEXT_EXTS = frozenset({".svg", ".json", ".md", ".txt", ".css", ".html", ".csv", ".yaml", ".yml"})
_OFFICE_EXTS = frozenset({".pdf", ".docx", ".xlsx"})


def _safe_zip_member_name(name: str) -> bool:
    """Reject absolute paths or '..' traversal inside ZIP archives."""
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = name.split("/")
    return ".." not in parts


def _parse_penpot_archive(filename: str, raw: bytes) -> dict[str, Any]:
    """Extract manifest, pages, tokens, and shape summary from a .penpot ZIP file."""
    buf = io.BytesIO(raw)
    if not zipfile.is_zipfile(buf):
        raise ValueError(f"{filename} no es un archivo .penpot (ZIP) válido.")

    buf.seek(0)
    manifest: dict[str, Any] = {}
    pages: list[str] = []
    files_meta: list[dict[str, Any]] = []
    colors_count = 0
    typographies_count = 0
    components_count = 0
    svg_entries: list[str] = []
    total_uncompressed = 0

    with zipfile.ZipFile(buf, "r") as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(f"El archivo .penpot excede el límite de {MAX_ZIP_ENTRIES} entradas.")

        for info in infos:
            if not _safe_zip_member_name(info.filename):
                raise ValueError(f"Ruta insegura detectada en .penpot: {info.filename}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("El archivo .penpot excede el límite descomprimido de 24 MB.")

            lower = info.filename.lower()
            if lower == "manifest.json":
                try:
                    manifest = json.loads(zf.read(info.filename).decode("utf-8", errors="replace"))
                except Exception:
                    manifest = {}
            elif lower.endswith(".svg"):
                svg_entries.append(info.filename)
            elif lower.endswith(".json") and "files/" in lower:
                try:
                    fdata = json.loads(zf.read(info.filename).decode("utf-8", errors="replace"))
                    if isinstance(fdata, dict):
                        fname = str(fdata.get("name") or info.filename)
                        raw_data = fdata.get("data")
                        data_obj: dict[str, Any] = raw_data if isinstance(raw_data, dict) else fdata
                        pages_index = data_obj.get("pagesIndex") or data_obj.get("pages-index") or {}
                        if isinstance(pages_index, dict):
                            for pval in pages_index.values():
                                if isinstance(pval, dict) and pval.get("name"):
                                    pages.append(str(pval["name"]))
                        colors = data_obj.get("colors") or {}
                        typos = data_obj.get("typographies") or {}
                        comps = data_obj.get("components") or {}
                        if isinstance(colors, dict):
                            colors_count += len(colors)
                        if isinstance(typos, dict):
                            typographies_count += len(typos)
                        if isinstance(comps, dict):
                            components_count += len(comps)
                        files_meta.append(
                            {
                                "name": fname,
                                "pages": len(pages_index) if isinstance(pages_index, dict) else 0,
                            }
                        )
                except Exception:
                    continue

    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    if not files_meta and manifest_files:
        for fid, fmeta in list(manifest_files.items())[:20]:
            if isinstance(fmeta, dict):
                files_meta.append({"id": fid, "name": fmeta.get("name", fid)})

    summary_lines = [
        f"Plantilla Penpot (.penpot): {filename} ({len(raw)} bytes)",
        f"- Archivos incluidos: {len(files_meta)} ({', '.join(str(f.get('name')) for f in files_meta[:5]) or 'N/A'})",
        f"- Páginas detectadas: {', '.join(pages[:10]) or 'Page 1'}",
        f"- Tokens/Assets: {colors_count} colores, {typographies_count} tipografías, {components_count} componentes, {len(svg_entries)} SVGs",
    ]
    context_text = "\n".join(summary_lines)[:MAX_CONTEXT_CHARS]
    return {
        "kind": "penpot_template",
        "summary": f"Plantilla .penpot ({len(files_meta)} archivos, {len(pages)} páginas, {components_count} componentes)",
        "context_text": context_text,
        "meta": {
            "files": files_meta[:10],
            "pages": pages[:20],
            "colors_count": colors_count,
            "typographies_count": typographies_count,
            "components_count": components_count,
            "svg_count": len(svg_entries),
        },
    }


def parse_uploaded_attachment(
    *,
    filename: str,
    content_bytes: bytes,
    content_type: str = "",
) -> dict[str, Any]:
    """Validate and parse an uploaded file into a chat context attachment."""
    clean_name = os.path.basename(filename or "attachment").strip() or "attachment"
    if len(content_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{clean_name} excede el máximo permitido de 16 MB.")

    ext = os.path.splitext(clean_name)[1].lower()
    att_id = f"att_{uuid.uuid4().hex[:12]}"

    if ext == ".penpot":
        parsed = _parse_penpot_archive(clean_name, content_bytes)
        return {
            "id": att_id,
            "filename": clean_name,
            "size_bytes": len(content_bytes),
            "content_type": content_type or "application/zip",
            **parsed,
        }

    if ext in _IMAGE_EXTS:
        mime = content_type or f"image/{ext.lstrip('.').replace('jpg', 'jpeg')}"
        b64_preview = base64.b64encode(content_bytes).decode("ascii") if len(content_bytes) <= 2_000_000 else ""
        return {
            "id": att_id,
            "filename": clean_name,
            "size_bytes": len(content_bytes),
            "content_type": mime,
            "kind": "image",
            "summary": f"Imagen de referencia {clean_name} ({round(len(content_bytes) / 1024, 1)} KB)",
            "context_text": f"Imagen adjunta: {clean_name} ({mime}, {len(content_bytes)} bytes).",
            "image_base64": b64_preview,
        }

    if ext in _TEXT_EXTS or (content_type and content_type.startswith("text/")):
        text = content_bytes.decode("utf-8", errors="replace")
        truncated = len(text) > MAX_CONTEXT_CHARS
        snippet = text[:MAX_CONTEXT_CHARS]
        kind = "svg" if ext == ".svg" else ("json" if ext == ".json" else "text")
        return {
            "id": att_id,
            "filename": clean_name,
            "size_bytes": len(content_bytes),
            "content_type": content_type or "text/plain",
            "kind": kind,
            "summary": f"Archivo {kind.upper()} {clean_name} ({round(len(content_bytes) / 1024, 1)} KB)",
            "context_text": f"--- {clean_name} ({kind}) ---\n{snippet}" + ("\n...[truncado]" if truncated else ""),
        }

    if ext in _OFFICE_EXTS:
        fmt_label = ext.lstrip(".").upper()
        return {
            "id": att_id,
            "filename": clean_name,
            "size_bytes": len(content_bytes),
            "content_type": content_type or "application/octet-stream",
            "kind": ext.lstrip("."),
            "summary": f"Documento {fmt_label} {clean_name} ({round(len(content_bytes) / 1024, 1)} KB)",
            "context_text": (
                f"Documento {fmt_label} adjunto: {clean_name} ({len(content_bytes)} bytes). "
                "Usa `design_suite_sandbox` (`doc_extract` o `pdf_render_pages`) con su `sandbox_path` para extraer texto/tablas o renderizar páginas."
            ),
        }

    raise ValueError(
        f"Formato no soportado ({ext or 'sin extensión'}). Usa .penpot, .svg, .json, .md, .txt, .csv, .yaml, .pdf, .docx, .xlsx o imagen."
    )
