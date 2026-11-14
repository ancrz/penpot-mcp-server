"""Canonical design tokens and provider/quick-action catalogs shared across Python and React (SDD)."""

from __future__ import annotations

from typing import Final

DESIGN_TOKENS: Final[dict[str, str]] = {
    "bg_0": "#090d16",
    "bg_1": "#111827",
    "bg_2": "#1a2436",
    "bg_3": "#243147",
    "border": "#2a364c",
    "border_strong": "#3b4b68",
    "text_0": "#f8fafc",
    "text_1": "#94a3b8",
    "accent": "#6366f1",
    "accent_hover": "#4f46e5",
    "accent_purple": "#8b5cf6",
    "ok": "#10b981",
    "ok_text": "#6ee7b7",
    "warn": "#f59e0b",
    "warn_text": "#fcd34d",
    "danger": "#f87171",
    "sky": "#38bdf8",
    "bubble_user_bg": "#2e2a72",
    "text_on_accent": "#ffffff",
}

PROVIDER_CATALOG: Final[dict[str, dict[str, object]]] = {
    "gemini": {
        "label": "Google Gemini",
        "models": ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
        "default_base_url": "https://generativelanguage.googleapis.com",
    },
    "openai": {
        "label": "OpenAI",
        "models": ["gpt-4.1", "gpt-4.1-mini", "o4-mini"],
        "default_base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-haiku-latest"],
        "default_base_url": "https://api.anthropic.com/v1",
    },
    "ollama": {
        "label": "Ollama Local",
        "models": ["gemma4:e2b", "qwen2.5-coder:7b", "llama3.2:3b"],
        "default_base_url": "http://localhost:11434/v1",
    },
}

QUICK_ACTIONS_CATALOG: Final[list[dict[str, str]]] = [
    {"id": "vision_scan", "label": "👁️ Ojos pgvector", "prompt": "Usa query_canvas_vision_pgvector para listar qué hay en el lienzo."},
    {"id": "create_hero_card", "label": "+ Hero Card", "prompt": "Crea una Hero Card moderna con título, subtítulo y CTA."},
    {"id": "create_login_form", "label": "+ Login Form", "prompt": "Crea un formulario de Login limpio con email, password y botón."},
    {"id": "create_pricing_card", "label": "+ Pricing Card", "prompt": "Crea una Pricing Card Pro con precio y lista de features."},
    {"id": "create_navbar", "label": "+ Navbar", "prompt": "Crea una barra de navegación superior responsiva con logo y links."},
    {"id": "bind_tokens", "label": "Bind Tokens", "prompt": "Sincroniza y enlaza tokens de color/tipografía al frame activo."},
    {"id": "inspect_css", "label": "Inspect CSS", "prompt": "Inspecciona el frame activo y devuelve su árbol y CSS."},
]
