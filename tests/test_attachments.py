"""Unit tests for uploaded attachment and .penpot template parsing."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from penpot_mcp.services.attachments import parse_uploaded_attachment


def _make_penpot_zip(extra_entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "type": "penpot/export-files",
                    "version": 1,
                    "files": [{"id": "file-1", "name": "Landing Template"}],
                }
            ),
        )
        zf.writestr(
            "files/demo.json",
            json.dumps(
                {
                    "name": "Landing Template",
                    "pagesIndex": {
                        "page-1": {"name": "Home"},
                        "page-2": {"name": "Pricing"},
                    },
                    "colors": {
                        "c1": {"name": "Brand Blue", "color": "#0EA5E9"},
                    },
                    "typographies": {
                        "t1": {"name": "Heading XL", "fontFamily": "Inter"},
                    },
                    "components": {
                        "cmp1": {"name": "HeroBanner"},
                    },
                }
            ),
        )
        zf.writestr(
            "assets/logo.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>',
        )
        if extra_entries:
            for name, payload in extra_entries.items():
                zf.writestr(name, payload)
    return buf.getvalue()


def test_parse_penpot_template_zip() -> None:
    raw = _make_penpot_zip()
    result = parse_uploaded_attachment(
        filename="landing-kit.penpot",
        content_bytes=raw,
        content_type="application/octet-stream",
    )

    assert result["kind"] == "penpot_template"
    assert result["filename"] == "landing-kit.penpot"
    assert result["size_bytes"] == len(raw)
    assert "Landing Template" in result["context_text"]
    assert "Home" in result["context_text"]
    assert "Pricing" in result["context_text"]
    assert "1 colores, 1 tipografías, 1 componentes, 1 SVGs" in result["context_text"]
    assert result["meta"]["colors_count"] == 1
    assert result["meta"]["typographies_count"] == 1
    assert result["meta"]["components_count"] == 1
    assert result["meta"]["svg_count"] == 1


def test_parse_svg_attachment() -> None:
    svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32"/></svg>'
    result = parse_uploaded_attachment(
        filename="icon.svg",
        content_bytes=svg_bytes,
        content_type="image/svg+xml",
    )

    assert result["kind"] == "svg"
    assert result["filename"] == "icon.svg"
    assert "<svg" in result["context_text"]


def test_reject_penpot_zip_slip() -> None:
    raw = _make_penpot_zip(extra_entries={"../escape.txt": b"pwned"})
    with pytest.raises(ValueError, match="Ruta insegura"):
        parse_uploaded_attachment(filename="evil.penpot", content_bytes=raw)


def test_reject_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="Formato no soportado"):
        parse_uploaded_attachment(filename="payload.exe", content_bytes=b"MZ\x90\x00")
