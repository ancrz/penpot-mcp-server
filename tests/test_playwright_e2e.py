"""End-to-end Playwright browser tests against the live Penpot AI Studio stack (:8787)."""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("PENPOT_MCP_UI_URL", "http://127.0.0.1:8787")
UI_URL = f"{BASE_URL}/plugin/ui.html"


def _launch_browser(p):
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


@pytest.mark.e2e
def test_e2e_ui_loads_tabs_and_architecture_status() -> None:
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page_errors: list[str] = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        resp = page.goto(UI_URL, wait_until="networkidle", timeout=20000)
        assert resp is not None and resp.status == 200

        # Chat IA tab is visible by default
        page.get_by_role("button", name="+ Nuevo Chat").wait_for(timeout=10000)
        composer = page.get_by_placeholder(
            re.compile(r"Describe el componente o suelta una plantilla")
        )
        assert composer.is_visible()

        # Navigate across all modular sub-tabs (SDD)
        page.get_by_role("tab", name=re.compile(r"Perfil")).click()
        page.get_by_placeholder("Ej. gemini-3.5-flash-lite").wait_for(timeout=5000)

        page.get_by_role("tab", name=re.compile(r"API Keys")).click()
        page.get_by_placeholder("custom-profile").wait_for(timeout=5000)

        page.get_by_role("tab", name=re.compile(r"Usuarios & pgvector")).click()
        page.get_by_placeholder(re.compile(r"Buscar elementos en el lienzo")).wait_for(timeout=5000)

        page.get_by_role("tab", name=re.compile(r"Arquitectura")).click()
        page.get_by_text("Python 3.14").wait_for(timeout=5000)
        assert page.get_by_text(re.compile(r"Capa agéntica:")).is_visible()

        assert page_errors == [], f"Unexpected browser page errors: {page_errors}"
        browser.close()


@pytest.mark.e2e
def test_e2e_multichat_create_reload_and_cleanup() -> None:
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(UI_URL, wait_until="networkidle", timeout=20000)

        before_resp = page.request.get(f"{BASE_URL}/agent/chats")
        assert before_resp.status == 200
        before_ids = {s["session_id"] for s in before_resp.json()["sessions"]}
        assert "default" in before_ids

        # Create a new chat session from the UI button
        page.get_by_role("button", name="+ Nuevo Chat").click()
        page.wait_for_timeout(800)

        after_resp = page.request.get(f"{BASE_URL}/agent/chats")
        assert after_resp.status == 200
        after_ids = {s["session_id"] for s in after_resp.json()["sessions"]}
        new_ids = after_ids - before_ids
        assert len(new_ids) == 1, f"Expected 1 new session, got {new_ids}"
        created_sid = next(iter(new_ids))

        # Reload browser and verify the newly created session is persisted in the selector
        page.reload(wait_until="networkidle", timeout=20000)
        option_locator = page.locator(f"select option[value='{created_sid}']")
        assert option_locator.count() == 1

        # Clean up test session so live DB remains tidy
        del_resp = page.request.delete(f"{BASE_URL}/agent/chats/{created_sid}")
        assert del_resp.status == 200
        browser.close()


@pytest.mark.e2e
def test_e2e_live_api_config_chats_and_memory_search() -> None:
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()

        status_resp = page.request.get(f"{BASE_URL}/agent/status")
        assert status_resp.status == 200
        status_data = status_resp.json()
        assert "agent_db" in status_data
        assert status_data["agent_db"]["backend"] in ("penpot_postgres", "sqlite", "postgres")

        mem_resp = page.request.get(f"{BASE_URL}/agent/memory/search?q=login&limit=3")
        assert mem_resp.status == 200
        mem_data = mem_resp.json()
        assert mem_data["status"] == "OK"
        assert isinstance(mem_data["hits"], list)

        browser.close()
