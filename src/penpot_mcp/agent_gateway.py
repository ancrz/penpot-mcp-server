"""Embedded SDK Agent Pipeline & AI Gateway for Penpot MCP Server.

RE Origin:
- /home/ancruz/Documents/workspaces/sdk-agent-pipeline/model_gateway/{providers.py,failover.py,signatures.py}
- /home/ancruz/Documents/workspaces/sdk-agent-pipeline/orchestrator/ratelimit.py
- /home/ancruz/Documents/workspaces/vanguard-control (AI Gateway & Chat session architecture)
- Figma AI ("Make Designs" / contextual Actions bar SDD specification)

Provides:
1. Multi-provider LLM routing with circuit breakers (Gemini primary -> Gemini backup -> OpenAI -> Anthropic -> Local Ollama -> Deterministic SDD Synthesizer).
2. Gemini 3 thoughtSignature backfill ('skip_thought_signature_validator') and empty-answer retry.
3. Multi-turn tool-calling agent loop wired directly to the 14 compressed Penpot MCP sub-tools, AEP Code Mode (`playbook_run`), and the live WebSocket Plugin bridge (`PenpotGateway`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
import re
import time
from typing import Any, Optional
import uuid

import httpx

from penpot_mcp.config import settings
from penpot_mcp.gateway import gateway

logger = logging.getLogger(__name__)

DUMMY_THOUGHT_SIGNATURE = "skip_thought_signature_validator"
EMPTY_RETRIES = 2
OUTAGE_CODES = {500, 502, 503, 504, 529}
FAILOVER_KINDS = {
    "rate",
    "quota_daily",
    "billing",
    "auth",
    "model_missing",
    "outage",
    "timeout",
    "network",
}

_ANNOUNCE_RE = re.compile(
    r"\b(let me|i will|i'll|i am going to|i'm going to|i need to|next,? i|now i|voy a|perm[ií]teme)\b",
    re.IGNORECASE,
)
NUDGE_PROMPT = (
    "Execute the tool call now with its arguments to materialize the change on the Penpot canvas. "
    "Do not merely announce the action."
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Failover Classification, Circuit Breakers & RPM Slot Limiter
#    (Adapted from sdk-agent-pipeline/model_gateway/failover.py & ratelimit.py)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RouteFailure:
    kind: str
    detail: str
    retry_after: Optional[float] = None


def classify_failure(
    status: Optional[int],
    body: Any = None,
    headers: Optional[httpx.Headers] = None,
    exc: Optional[BaseException] = None,
) -> RouteFailure:
    """Classify a provider HTTP status or transport exception into a failover kind."""
    if exc is not None:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return RouteFailure("network", f"unreachable: {type(exc).__name__}")
        if isinstance(exc, httpx.TimeoutException):
            return RouteFailure("timeout", f"timed out: {type(exc).__name__}")
        return RouteFailure("network", f"transport error: {type(exc).__name__}")

    try:
        text = json.dumps(body)[:3000].lower() if body is not None else ""
    except (TypeError, ValueError):
        text = ""

    retry_after: Optional[float] = None
    if headers is not None and headers.get("retry-after", "").strip().isdigit():
        retry_after = float(headers["retry-after"])

    if status == 429:
        if "insufficient_quota" in text or "billing" in text:
            return RouteFailure("billing", "429 billing/credit exhausted")
        if "perday" in text or "per day" in text or "daily" in text:
            return RouteFailure("quota_daily", "429 daily quota exhausted")
        return RouteFailure("rate", "429 rate limited", retry_after)
    if status == 402 or (status == 400 and "credit balance" in text):
        return RouteFailure("billing", f"{status} billing/credit exhausted")
    if status in (401, 403) or (
        status == 400
        and ("api_key_invalid" in text or "api key not valid" in text or "api key expired" in text)
    ):
        return RouteFailure("auth", f"{status} key invalid or unauthorized")
    if status == 404:
        return RouteFailure("model_missing", "404 model not available for this key")
    if status in OUTAGE_CODES or (status is not None and status >= 500):
        return RouteFailure("outage", f"{status} provider error")
    if status == 408:
        return RouteFailure("timeout", "408 provider timeout")
    return RouteFailure("client", f"HTTP {status}")


class CircuitBreakers:
    """In-memory scoped circuit breakers for model provider routes."""

    def __init__(self, clock: Any = time.time) -> None:
        self.clock = clock
        self._breakers: dict[str, dict[str, Any]] = {}

    def open_for_failure(
        self, account: str, provider: str, model: str, failure: RouteFailure
    ) -> dict[str, Any]:
        cooldown_map = {
            "rate": max(10.0, failure.retry_after or 30.0),
            "quota_daily": 1800.0,
            "billing": 1800.0,
            "auth": 1800.0,
            "model_missing": 1800.0,
            "outage": 20.0,
            "timeout": 20.0,
            "network": 20.0,
        }
        seconds = cooldown_map.get(failure.kind, 30.0)
        scope = (
            f"account:{account}"
            if failure.kind in ("billing", "auth")
            else f"account:{account}:{model}"
            if failure.kind in ("rate", "quota_daily", "model_missing")
            else f"provider:{provider}:{model}"
        )
        entry = {
            "scope": scope,
            "kind": failure.kind,
            "detail": failure.detail[:240],
            "until": self.clock() + seconds,
        }
        self._breakers[scope] = entry
        return entry

    def blocking(self, account: str, provider: str, model: str) -> Optional[dict[str, Any]]:
        now = self.clock()
        for scope in (
            f"account:{account}",
            f"account:{account}:{model}",
            f"provider:{provider}",
            f"provider:{provider}:{model}",
        ):
            entry = self._breakers.get(scope)
            if entry:
                if entry["until"] > now:
                    return entry
                self._breakers.pop(scope, None)
        return None

    def active_list(self) -> list[dict[str, Any]]:
        now = self.clock()
        out: list[dict[str, Any]] = []
        for scope, entry in list(self._breakers.items()):
            if entry["until"] > now:
                out.append({**entry, "seconds_left": max(0, int(entry["until"] - now))})
            else:
                self._breakers.pop(scope, None)
        return out

    def reset(self, scope: Optional[str] = None) -> int:
        if not scope:
            count = len(self._breakers)
            self._breakers.clear()
            return count
        removed = [k for k in list(self._breakers.keys()) if k == scope or k.startswith(f"{scope}:")]
        for k in removed:
            self._breakers.pop(k, None)
        return len(removed)


class InProcessRpmLimiter:
    """Spaces requests at 60/rpm seconds per bucket without blocking the event loop."""

    def __init__(self) -> None:
        self._next_slot: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, rpm: int) -> float:
        if rpm <= 0:
            return 0.0
        interval = 60.0 / max(1, rpm)
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot.get(key, 0.0))
            self._next_slot[key] = slot + interval
        wait_s = slot - now
        if 0 < wait_s <= 5.0:
            await asyncio.sleep(wait_s)
            return wait_s
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. Gemini REST Protocol & Provider Translators
#    (Adapted from sdk-agent-pipeline/model_gateway/{providers.py,signatures.py})
# ═══════════════════════════════════════════════════════════════════════════

_SCHEMA_KEYS = {
    "type",
    "description",
    "properties",
    "items",
    "required",
    "enum",
    "format",
    "nullable",
    "anyOf",
    "minimum",
    "maximum",
    "default",
    "title",
}


def to_json_schema(schema: Any) -> Any:
    """Convert Gemini Schema (uppercase types) to standard JSON Schema."""
    if isinstance(schema, list):
        return [to_json_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS or key == "nullable":
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties":
            out["properties"] = {k: to_json_schema(v) for k, v in (value or {}).items()}
        elif key in ("items", "anyOf"):
            out[key] = to_json_schema(value)
        else:
            out[key] = value
    if schema.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def backfill_thought_signatures(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Ensure the first functionCall in every model step carries a thoughtSignature for Gemini 3."""
    contents = body.get("contents")
    if not isinstance(contents, list):
        return body, 0
    added = 0
    for content in contents:
        if not isinstance(content, dict) or content.get("role") != "model":
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and ("functionCall" in part or "function_call" in part):
                if not (part.get("thoughtSignature") or part.get("thought_signature")):
                    part["thoughtSignature"] = DUMMY_THOUGHT_SIGNATURE
                    added += 1
                break
    return body, added


def is_empty_gemini_answer(payload: Any) -> bool:
    """Return True if a 200 Gemini response has finishReason STOP but zero content parts."""
    if not isinstance(payload, dict) or (payload.get("promptFeedback") or {}).get("blockReason"):
        return False
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return False
    for c in candidates:
        if not isinstance(c, dict) or c.get("finishReason") not in (None, "STOP"):
            return False
        if (c.get("content") or {}).get("parts"):
            return False
    return True


def gemini_to_openai(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate a Gemini generateContent request body into OpenAI chat completions format."""
    messages: list[dict[str, Any]] = []
    system = " ".join(
        p.get("text", "") for p in (body.get("systemInstruction") or {}).get("parts") or []
    ).strip()
    if system:
        messages.append({"role": "system", "content": system})
    pending: dict[str, list[str]] = {}
    for content in body.get("contents") or []:
        role = content.get("role", "user")
        texts, calls, results = [], [], []
        for part in content.get("parts") or []:
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                call_id = fc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                pending.setdefault(fc["name"], []).append(call_id)
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fc["name"],
                            "arguments": json.dumps(fc.get("args") or {}),
                        },
                    }
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                queue = pending.get(fr["name"]) or []
                call_id = fr.get("id") or (
                    queue.pop(0) if queue else f"call_{uuid.uuid4().hex[:12]}"
                )
                if fr.get("id") and fr["id"] in queue:
                    queue.remove(fr["id"])
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(fr.get("response") or {}),
                    }
                )
        if role == "model":
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                msg["tool_calls"] = calls
            messages.append(msg)
        else:
            messages.extend(results)
            if texts:
                messages.append({"role": "user", "content": "\n".join(texts)})
    req: dict[str, Any] = {"model": model, "messages": messages}
    tools = [
        {
            "type": "function",
            "function": {
                "name": fd["name"],
                "description": fd.get("description", ""),
                "parameters": to_json_schema(
                    fd.get("parametersJsonSchema")
                    or fd.get("parameters")
                    or {"type": "object", "properties": {}}
                ),
            },
        }
        for tool in body.get("tools") or []
        for fd in tool.get("functionDeclarations") or []
    ]
    if tools:
        req["tools"] = tools
    cfg = body.get("generationConfig") or {}
    if "temperature" in cfg:
        req["temperature"] = cfg["temperature"]
    if "maxOutputTokens" in cfg:
        req["max_tokens"] = cfg["maxOutputTokens"]
    return req


def openai_to_gemini(response: dict[str, Any]) -> dict[str, Any]:
    """Translate an OpenAI chat completion response back into Gemini generateContent shape."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    parts: list[dict[str, Any]] = []
    if message.get("content"):
        parts.append({"text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"_raw": fn.get("arguments")}
        parts.append(
            {
                "functionCall": {
                    "id": call.get("id"),
                    "name": fn.get("name"),
                    "args": args,
                },
                "thoughtSignature": DUMMY_THOUGHT_SIGNATURE,
            }
        )
    usage = response.get("usage") or {}
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": ""}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
        },
        "modelVersion": response.get("model"),
    }


def gemini_to_anthropic(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate a Gemini generateContent request into Anthropic Messages API format."""
    messages: list[dict[str, Any]] = []
    pending: dict[str, list[str]] = {}
    for content in body.get("contents") or []:
        role = "assistant" if content.get("role") == "model" else "user"
        blocks: list[dict[str, Any]] = []
        for part in content.get("parts") or []:
            if "text" in part and not part.get("thought") and part["text"]:
                blocks.append({"type": "text", "text": part["text"]})
            elif "functionCall" in part:
                fc = part["functionCall"]
                tid = f"toolu_{uuid.uuid4().hex[:20]}"
                pending.setdefault(fc["name"], []).append(tid)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tid,
                        "name": fc["name"],
                        "input": fc.get("args") or {},
                    }
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                queue = pending.get(fr["name"]) or []
                tid = queue.pop(0) if queue else f"toolu_{uuid.uuid4().hex[:20]}"
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": json.dumps(fr.get("response") or {}),
                    }
                )
        blocks.sort(key=lambda b: 0 if b["type"] == "tool_result" else 1)
        if blocks:
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
    cfg = body.get("generationConfig") or {}
    req: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(cfg.get("maxOutputTokens") or 2048),
    }
    system = " ".join(
        p.get("text", "") for p in (body.get("systemInstruction") or {}).get("parts") or []
    ).strip()
    if system:
        req["system"] = system
    tools = [
        {
            "name": fd["name"],
            "description": fd.get("description", ""),
            "input_schema": to_json_schema(
                fd.get("parametersJsonSchema")
                or fd.get("parameters")
                or {"type": "object", "properties": {}}
            ),
        }
        for tool in body.get("tools") or []
        for fd in tool.get("functionDeclarations") or []
    ]
    if tools:
        req["tools"] = tools
    return req


def anthropic_to_gemini(response: dict[str, Any]) -> dict[str, Any]:
    """Translate an Anthropic Messages API response into Gemini generateContent shape."""
    parts: list[dict[str, Any]] = []
    for block in response.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            parts.append({"text": block["text"]})
        elif block.get("type") == "tool_use":
            parts.append(
                {
                    "functionCall": {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "args": block.get("input") or {},
                    },
                    "thoughtSignature": DUMMY_THOUGHT_SIGNATURE,
                }
            )
    usage = response.get("usage") or {}
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": ""}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": usage.get("input_tokens", 0),
            "candidatesTokenCount": usage.get("output_tokens", 0),
            "totalTokenCount": (usage.get("input_tokens", 0) or 0)
            + (usage.get("output_tokens", 0) or 0),
        },
        "modelVersion": response.get("model"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. Deterministic SDD Layout Templates (1-Turn Code Mode Playbooks)
# ═══════════════════════════════════════════════════════════════════════════


def build_ui_template_steps(
    template_kind: str,
    file_id: str,
    page_id: str,
    title: str | None = None,
    subtitle: str | None = None,
    cta_text: str | None = None,
    accent_color: str = "#6366F1",
    x: float = 80,
    y: float = 80,
) -> tuple[str, list[dict[str, Any]]]:
    """Compile a semantic UI artifact specification into an atomic playbook_run(action='code') DAG."""
    kind = (template_kind or "hero_card").strip().lower()

    if kind in ("login_form", "login", "auth", "formulario"):
        card_title = title or "Sign In to Workspace"
        card_sub = subtitle or "Enter your credentials to access Penpot AI Studio"
        btn_label = cta_text or "Continue with SSO ->"
        return (
            "login-form-artifact",
            [
                {
                    "id": "card_frame",
                    "tool": "create",
                    "store_as": "login_frame",
                    "params": {
                        "action": "frame",
                        "file_id": file_id,
                        "page_id": page_id,
                        "name": f"AI Login Card — {card_title[:24]}",
                        "x": x,
                        "y": y,
                        "width": 420,
                        "height": 440,
                        "fill_color": "#0F172A",
                        "border_radius": 20,
                    },
                },
                {
                    "id": "header_pill",
                    "tool": "create",
                    "depends_on": ["card_frame"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Security Badge",
                        "x": x + 32,
                        "y": y + 28,
                        "width": 160,
                        "height": 28,
                        "fill_color": "#1E293B",
                        "border_radius": 14,
                    },
                },
                {
                    "id": "title_txt",
                    "tool": "create",
                    "depends_on": ["card_frame"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Login Title",
                        "text": card_title,
                        "x": x + 32,
                        "y": y + 74,
                        "font_size": 24,
                        "font_weight": "700",
                        "fill_color": "#F8FAFC",
                    },
                },
                {
                    "id": "sub_txt",
                    "tool": "create",
                    "depends_on": ["title_txt"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Login Subtitle",
                        "text": card_sub,
                        "x": x + 32,
                        "y": y + 112,
                        "font_size": 13,
                        "font_weight": "400",
                        "fill_color": "#94A3B8",
                    },
                },
                {
                    "id": "email_input",
                    "tool": "create",
                    "depends_on": ["sub_txt"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Email Input Field",
                        "x": x + 32,
                        "y": y + 162,
                        "width": 356,
                        "height": 48,
                        "fill_color": "#1E293B",
                        "stroke_color": "#334155",
                        "stroke_width": 1,
                        "border_radius": 10,
                    },
                },
                {
                    "id": "pass_input",
                    "tool": "create",
                    "depends_on": ["email_input"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Password Input Field",
                        "x": x + 32,
                        "y": y + 228,
                        "width": 356,
                        "height": 48,
                        "fill_color": "#1E293B",
                        "stroke_color": "#334155",
                        "stroke_width": 1,
                        "border_radius": 10,
                    },
                },
                {
                    "id": "submit_btn",
                    "tool": "create",
                    "depends_on": ["pass_input"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Primary Submit CTA",
                        "x": x + 32,
                        "y": y + 306,
                        "width": 356,
                        "height": 52,
                        "fill_color": accent_color,
                        "border_radius": 12,
                    },
                },
                {
                    "id": "submit_txt",
                    "tool": "create",
                    "depends_on": ["submit_btn"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Submit Label",
                        "text": btn_label,
                        "x": x + 135,
                        "y": y + 323,
                        "font_size": 15,
                        "font_weight": "700",
                        "fill_color": "#FFFFFF",
                    },
                },
            ],
        )

    if kind in ("pricing_card", "pricing", "plan", "precios"):
        card_title = title or "Enterprise AI Plan"
        card_sub = subtitle or "$49 / month · Unlimited MCP Code Mode + Live Plugin"
        btn_label = cta_text or "Upgrade Workspace ->"
        return (
            "pricing-card-artifact",
            [
                {
                    "id": "card_frame",
                    "tool": "create",
                    "store_as": "pricing_frame",
                    "params": {
                        "action": "frame",
                        "file_id": file_id,
                        "page_id": page_id,
                        "name": f"AI Pricing Card — {card_title[:24]}",
                        "x": x,
                        "y": y,
                        "width": 380,
                        "height": 420,
                        "fill_color": "#090D16",
                        "stroke_color": accent_color,
                        "stroke_width": 2,
                        "border_radius": 20,
                    },
                },
                {
                    "id": "tier_badge",
                    "tool": "create",
                    "depends_on": ["card_frame"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Tier Pill",
                        "x": x + 28,
                        "y": y + 28,
                        "width": 136,
                        "height": 28,
                        "fill_color": accent_color,
                        "border_radius": 14,
                    },
                },
                {
                    "id": "title_txt",
                    "tool": "create",
                    "depends_on": ["card_frame"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Plan Name",
                        "text": card_title,
                        "x": x + 28,
                        "y": y + 76,
                        "font_size": 24,
                        "font_weight": "700",
                        "fill_color": "#F8FAFC",
                    },
                },
                {
                    "id": "price_txt",
                    "tool": "create",
                    "depends_on": ["title_txt"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Price Tag",
                        "text": card_sub,
                        "x": x + 28,
                        "y": y + 118,
                        "font_size": 14,
                        "font_weight": "400",
                        "fill_color": "#38BDF8",
                    },
                },
                {
                    "id": "cta_btn",
                    "tool": "create",
                    "depends_on": ["price_txt"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Pricing CTA Button",
                        "x": x + 28,
                        "y": y + 332,
                        "width": 324,
                        "height": 52,
                        "fill_color": "#10B981",
                        "border_radius": 12,
                    },
                },
                {
                    "id": "cta_txt",
                    "tool": "create",
                    "depends_on": ["cta_btn"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Pricing CTA Text",
                        "text": btn_label,
                        "x": x + 98,
                        "y": y + 349,
                        "font_size": 15,
                        "font_weight": "700",
                        "fill_color": "#052E16",
                    },
                },
            ],
        )

    if kind in ("navbar", "nav", "header", "barra"):
        nav_title = title or "Penpot AI Studio"
        nav_sub = subtitle or "Dashboard    Components    Tokens    Gateway"
        btn_label = cta_text or "Launch Agent"
        return (
            "navbar-artifact",
            [
                {
                    "id": "card_frame",
                    "tool": "create",
                    "store_as": "navbar_frame",
                    "params": {
                        "action": "frame",
                        "file_id": file_id,
                        "page_id": page_id,
                        "name": f"AI Navigation Bar — {nav_title[:24]}",
                        "x": x,
                        "y": y,
                        "width": 960,
                        "height": 76,
                        "fill_color": "#0F172A",
                        "border_radius": 16,
                    },
                },
                {
                    "id": "brand_txt",
                    "tool": "create",
                    "depends_on": ["card_frame"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Brand Logo Text",
                        "text": nav_title,
                        "x": x + 28,
                        "y": y + 26,
                        "font_size": 20,
                        "font_weight": "700",
                        "fill_color": "#F8FAFC",
                    },
                },
                {
                    "id": "links_txt",
                    "tool": "create",
                    "depends_on": ["brand_txt"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Nav Links",
                        "text": nav_sub,
                        "x": x + 310,
                        "y": y + 29,
                        "font_size": 14,
                        "font_weight": "400",
                        "fill_color": "#94A3B8",
                    },
                },
                {
                    "id": "cta_btn",
                    "tool": "create",
                    "depends_on": ["links_txt"],
                    "params": {
                        "action": "rectangle",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Nav Action Button",
                        "x": x + 780,
                        "y": y + 16,
                        "width": 152,
                        "height": 44,
                        "fill_color": accent_color,
                        "border_radius": 10,
                    },
                },
                {
                    "id": "cta_txt",
                    "tool": "create",
                    "depends_on": ["cta_btn"],
                    "params": {
                        "action": "text",
                        "file_id": file_id,
                        "page_id": page_id,
                        "parent_id": "$steps.card_frame.shape_id",
                        "name": "Nav Action Label",
                        "text": btn_label,
                        "x": x + 806,
                        "y": y + 29,
                        "font_size": 14,
                        "font_weight": "700",
                        "fill_color": "#FFFFFF",
                    },
                },
            ],
        )

    # Default: Hero Card ("hero_card" | "card" | "custom")
    card_title = title or "Penpot AI Studio + SDK Agent Pipeline"
    card_sub = subtitle or "Generated via Hybrid Gateway & 1-Turn AEP Code Mode"
    btn_label = cta_text or "Explore Artifact ->"
    return (
        "hero-card-artifact",
        [
            {
                "id": "card_frame",
                "tool": "create",
                "store_as": "hero_frame",
                "params": {
                    "action": "frame",
                    "file_id": file_id,
                    "page_id": page_id,
                    "name": f"AI Hero Card — {card_title[:28]}",
                    "x": x,
                    "y": y,
                    "width": 520,
                    "height": 300,
                    "fill_color": "#0F172A",
                    "border_radius": 20,
                },
            },
            {
                "id": "accent_banner",
                "tool": "create",
                "depends_on": ["card_frame"],
                "params": {
                    "action": "rectangle",
                    "file_id": file_id,
                    "page_id": page_id,
                    "parent_id": "$steps.card_frame.shape_id",
                    "name": "Accent Header Pill",
                    "x": x + 28,
                    "y": y + 28,
                    "width": 190,
                    "height": 30,
                    "fill_color": accent_color,
                    "border_radius": 15,
                },
            },
            {
                "id": "headline_txt",
                "tool": "create",
                "depends_on": ["accent_banner"],
                "params": {
                    "action": "text",
                    "file_id": file_id,
                    "page_id": page_id,
                    "parent_id": "$steps.card_frame.shape_id",
                    "name": "Headline Text",
                    "text": card_title,
                    "x": x + 28,
                    "y": y + 78,
                    "font_size": 24,
                    "font_weight": "700",
                    "fill_color": "#F8FAFC",
                },
            },
            {
                "id": "subtitle_txt",
                "tool": "create",
                "depends_on": ["headline_txt"],
                "params": {
                    "action": "text",
                    "file_id": file_id,
                    "page_id": page_id,
                    "parent_id": "$steps.card_frame.shape_id",
                    "name": "Subtitle Text",
                    "text": card_sub,
                    "x": x + 28,
                    "y": y + 122,
                    "font_size": 14,
                    "font_weight": "400",
                    "fill_color": "#94A3B8",
                },
            },
            {
                "id": "cta_button",
                "tool": "create",
                "depends_on": ["subtitle_txt"],
                "params": {
                    "action": "rectangle",
                    "file_id": file_id,
                    "page_id": page_id,
                    "parent_id": "$steps.card_frame.shape_id",
                    "name": "CTA Button",
                    "x": x + 28,
                    "y": y + 210,
                    "width": 220,
                    "height": 52,
                    "fill_color": "#10B981",
                    "border_radius": 12,
                },
            },
            {
                "id": "cta_label",
                "tool": "create",
                "depends_on": ["cta_button"],
                "params": {
                    "action": "text",
                    "file_id": file_id,
                    "page_id": page_id,
                    "parent_id": "$steps.card_frame.shape_id",
                    "name": "CTA Label",
                    "text": btn_label,
                    "x": x + 56,
                    "y": y + 227,
                    "font_size": 15,
                    "font_weight": "700",
                    "fill_color": "#052E16",
                },
            },
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Agent Tool Declarations (Gemini Function Declarations)
# ═══════════════════════════════════════════════════════════════════════════

AGENT_FUNCTION_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "create_ui_artifact",
        "description": (
            "Create a complete, multi-layer UI artifact (Hero Card, Login Form, Pricing Card, or Navigation Bar) "
            "on the active Penpot page in a single atomic Code Mode batch turn."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "template_kind": {
                    "type": "STRING",
                    "description": "Template type: 'hero_card', 'login_form', 'pricing_card', or 'navbar'.",
                },
                "title": {
                    "type": "STRING",
                    "description": "Primary headline or title text for the UI component.",
                },
                "subtitle": {
                    "type": "STRING",
                    "description": "Secondary subtitle, price, or navigation links text.",
                },
                "cta_text": {
                    "type": "STRING",
                    "description": "Call-to-action button label.",
                },
                "accent_color": {
                    "type": "STRING",
                    "description": "6-digit hex color for primary accents (e.g. '#6366F1').",
                },
                "x": {"type": "NUMBER", "description": "Canvas X coordinate (default 80)."},
                "y": {"type": "NUMBER", "description": "Canvas Y coordinate (default 80)."},
            },
            "required": ["template_kind"],
        },
    },
    {
        "name": "call_penpot_branch_tool",
        "description": (
            "Call any of the 13 domain Penpot MCP sub-tools ('discover', 'projects', 'files', 'shapes', "
            "'create', 'modify', 'text', 'assets', 'comments', 'media', 'export', 'system', 'plugin')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tool": {
                    "type": "STRING",
                    "description": "Branch tool name (e.g. 'create', 'modify', 'shapes', 'assets', 'plugin').",
                },
                "action": {
                    "type": "STRING",
                    "description": "Action within the branch tool (e.g. 'frame', 'rectangle', 'text', 'fill', 'list', 'css').",
                },
                "params": {
                    "type": "OBJECT",
                    "description": "Additional keyword parameters for the branch tool action.",
                },
            },
            "required": ["tool", "action"],
        },
    },
    {
        "name": "run_penpot_batch",
        "description": (
            "Execute a multi-step DAG batch via playbook_run(action='code') with $steps.<id>.shape_id piping."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Batch name."},
                "steps": {
                    "type": "ARRAY",
                    "description": "Ordered list of step dicts ({id, tool, depends_on, params}).",
                    "items": {"type": "OBJECT"},
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "query_canvas_vision_pgvector",
        "description": (
            "Query the PostgreSQL pgvector semantic + spatial vision index of the active Penpot page "
            "to inspect what shapes, frames, colors, and texts are currently visible on the canvas."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Natural language or visual query (e.g. 'purple CTA button', 'hero card title', 'free space').",
                },
                "top_k": {
                    "type": "INTEGER",
                    "description": "Maximum number of matching shapes to return (default 10).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "media_sandbox_ffmpeg",
        "description": (
            "AEP Code Mode FFmpeg MCP sandbox (/sandbox): run single actions ('probe', 'resize', "
            "'crop', 'convert', 'extract_frame') or multi-step 'pipeline' DAGs with "
            "$steps.<id>.output_path piping and compact token output."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": "Code Mode: 'call' (single), 'pipeline' (multi-step DAG), or 'discover'.",
                },
                "action": {
                    "type": "STRING",
                    "description": "Action for mode='call': probe, resize, crop, convert, extract_frame.",
                },
                "input_path": {
                    "type": "STRING",
                    "description": "Relative sandbox path (e.g. 'uploads/<id>_logo.png').",
                },
                "output_name": {
                    "type": "STRING",
                    "description": "Output filename inside sandbox outputs/.",
                },
                "width": {"type": "INTEGER", "description": "Target width in pixels."},
                "height": {"type": "INTEGER", "description": "Target height in pixels (-1 keeps aspect)."},
                "x": {"type": "INTEGER", "description": "Crop X offset."},
                "y": {"type": "INTEGER", "description": "Crop Y offset."},
                "format": {"type": "STRING", "description": "Target format for convert (webp, png, jpg, avif)."},
                "quality": {"type": "INTEGER", "description": "Compression quality 1-100 (default 85)."},
                "timestamp": {"type": "STRING", "description": "Timestamp for extract_frame."},
                "steps": {
                    "type": "ARRAY",
                    "description": "Pipeline steps [{id, action, ...params}] when mode='pipeline'.",
                    "items": {"type": "OBJECT"},
                },
                "verbosity": {
                    "type": "STRING",
                    "description": "Token budget mode: 'minimal', 'summary' (default), or 'full'.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "design_suite_sandbox",
        "description": (
            "AEP Code Mode Design & Document Suite MCP (/sandbox): SVG optimization ('svg_optimize'), "
            "raster-to-SVG tracing ('raster_to_svg'), color palette extraction ('extract_palette'), "
            "WCAG contrast ('wcag_contrast'), background removal ('remove_background'), PDF/DOCX/XLSX/CSV "
            "extraction ('doc_extract'), PDF-to-PNG ('pdf_render_pages'), and bounded dataset query "
            "('data_sandbox_query'). Supports single 'call' or multi-step 'pipeline' with $steps.<id>.output_path."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": "Code Mode: 'call' (single), 'pipeline' (multi-step DAG), or 'discover'.",
                },
                "action": {
                    "type": "STRING",
                    "description": "Suite action name.",
                },
                "params": {
                    "type": "OBJECT",
                    "description": "Action parameters (e.g. input_path, output_name, fg_hex, bg_hex, n_colors).",
                },
                "steps": {
                    "type": "ARRAY",
                    "description": "Pipeline steps [{id, action, ...params}] when mode='pipeline'.",
                    "items": {"type": "OBJECT"},
                },
                "verbosity": {
                    "type": "STRING",
                    "description": "Token budget mode: 'minimal', 'summary' (default), or 'full'.",
                },
            },
            "required": [],
        },
    },
]


@dataclass
class ProviderRoute:
    tier: str
    provider: str
    account: str
    models: list[str]
    api_key: str = ""
    base_url: str = ""
    cloud: bool = True


@dataclass
class AgentTurnResult:
    status: str
    reply: str
    provider_used: str
    model_used: str
    tier_used: str
    file_id: Optional[str] = None
    page_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_fullname: Optional[str] = None
    vision_summary: Optional[dict[str, Any]] = None
    created_shapes: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sandbox_persistent: bool = False
    canvas_delta: Optional[dict[str, Any]] = None
    fallback_reasons: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


PROVIDERS_CATALOG: list[dict[str, Any]] = [
    {
        "id": "gemini",
        "label": "Google Gemini (AI Studio)",
        "freeTier": True,
        "default_base_url": "https://generativelanguage.googleapis.com",
        "default_models": ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
        "note": "Has a free tier with low RPM and paid tier by use.",
    },
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "freeTier": False,
        "default_base_url": "https://api.anthropic.com/v1",
        "default_models": ["claude-sonnet-4-5", "claude-3-5-haiku-latest", "claude-opus-4-5"],
        "note": "Billed by use; native Anthropic Messages API.",
    },
    {
        "id": "openai",
        "label": "OpenAI (ChatGPT)",
        "freeTier": False,
        "default_base_url": "https://api.openai.com/v1",
        "default_models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
        "note": "Billed by use; OpenAI Chat Completions API.",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "freeTier": False,
        "default_base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "Billed by use; OpenAI-compatible protocol.",
    },
    {
        "id": "openai_compatible",
        "label": "Other OpenAI-Compatible (Groq / OpenRouter)",
        "freeTier": False,
        "default_base_url": "https://api.openai.com/v1",
        "default_models": ["gpt-4o-mini", "llama-3.3-70b-versatile"],
        "note": "Any HTTPS base URL that speaks /chat/completions.",
    },
    {
        "id": "local",
        "label": "Local Model Server (Ollama / llama.cpp)",
        "freeTier": False,
        "default_base_url": "http://host.docker.internal:11434/v1",
        "default_models": ["gemma4:e2b", "qwen2.5-coder:7b", "llama3.2:3b"],
        "note": "No key, no quota, runs locally without internet.",
    },
]

PRESETS_CATALOG: list[dict[str, Any]] = [
    {
        "name": "auto",
        "kind": "routed",
        "description": "Cloud primary + backup accounts with automatic SDD deterministic fallback.",
    },
    {
        "name": "demo-free",
        "kind": "routed",
        "description": "Google Gemini AI Studio free tier (low RPM) with backup failover.",
    },
    {
        "name": "paid",
        "kind": "routed",
        "description": "Production cloud models (Claude Sonnet 4.5, ChatGPT GPT-4o, Gemini Pro).",
    },
    {
        "name": "local",
        "kind": "routed",
        "description": "Local Ollama / vLLM server only (zero cloud traffic).",
    },
    {
        "name": "offline",
        "kind": "offline",
        "description": "Deterministic SDD Code Mode stand-in (zero tokens, instant execution).",
    },
]

BASE_AGENT_INSTRUCTION = (
    "You are Penpot AI Studio, an expert UI/UX design agent embedded inside Penpot via the "
    "Penpot MCP Hybrid Gateway and SDK Agent Pipeline.\n"
    "Rules:\n"
    "1. When the user asks to create a card, hero section, login form, pricing card, navbar, or UI layout, "
    "call `create_ui_artifact` or `run_penpot_batch` immediately to materialize it on the active page.\n"
    "2. When modifying selected shapes, inspecting CSS/SVG, or binding tokens, call `call_penpot_branch_tool`.\n"
    "3. Keep your final response concise and in the user's language (Spanish or English)."
)


def _mask_last4(secret: str) -> Optional[str]:
    cleaned = (secret or "").strip()
    if not cleaned:
        return None
    return cleaned[-4:] if len(cleaned) >= 4 else "****"


class PenpotAgentGateway:
    """Modular SDK Agent Pipeline & AI Gateway embedded in Penpot MCP Server."""

    def __init__(self) -> None:
        self.breakers = CircuitBreakers()
        self.rpm_limiter = InProcessRpmLimiter()
        self.serving_state: dict[str, Any] = {
            "tier": "primary",
            "provider": "gemini",
            "model": settings.penpot_agent_model,
            "calls_total": 0,
            "last_latency_ms": 0.0,
        }
        self._config_path = "/tmp/penpot_ai_gateway_profiles.json"
        self.credentials: dict[str, dict[str, Any]] = {}
        self.active_profile: dict[str, Any] = {
            "preset": (settings.penpot_agent_mode or "auto").strip().lower(),
            "account": "google-primary",
            "provider": "gemini",
            "model": (settings.penpot_agent_model or "gemini-3.5-flash-lite").strip(),
            "fallback_model": (settings.penpot_agent_fallback_model or "gemini-2.5-flash").strip(),
            "rpm": int(settings.penpot_agent_rpm or 15),
        }
        self.agent_instructions: dict[str, Any] = {
            "base": BASE_AGENT_INSTRUCTION,
            "addendum": "",
            "version": 1,
            "note": "Base instruction (.env default)",
            "updated_at": None,
            "history": [],
        }
        self.active_user: dict[str, Any] = {
            "id": None,
            "email": settings.penpot_email or "anthony_cruz@outlook.com",
            "fullname": "Anthony Cruz",
            "lang": "es",
            "theme": "default",
            "resolved_via": "default",
        }
        self.user_profiles_cache: dict[str, dict[str, Any]] = {}
        self.attachments_cache: dict[str, dict[str, Any]] = {}
        self._seed_from_env()
        self._load_persisted_state()

    def _seed_from_env(self) -> None:
        """Seed initial provider credentials from .env settings."""
        primary_model = (settings.penpot_agent_model or "gemini-3.5-flash-lite").strip()
        fallback_model = (settings.penpot_agent_fallback_model or "gemini-2.5-flash").strip()
        g_models = [primary_model] + (
            [fallback_model] if fallback_model and fallback_model != primary_model else []
        )
        for m in ("gemini-2.5-flash", "gemini-2.5-pro"):
            if m not in g_models:
                g_models.append(m)

        if settings.google_api_key.strip():
            self.credentials["google-primary"] = {
                "name": "google-primary",
                "provider": "gemini",
                "tier": "free",
                "purpose": "primary",
                "description": "Primary Google AI Studio key (.env)",
                "api_key": settings.google_api_key.strip(),
                "base_url": settings.google_gemini_base_url.rstrip("/"),
                "models": list(g_models),
                "source": "env",
            }
        if settings.google_api_key_backup.strip():
            self.credentials["google-backup"] = {
                "name": "google-backup",
                "provider": "gemini",
                "tier": "free",
                "purpose": "backup",
                "description": "Backup Google AI Studio key (.env)",
                "api_key": settings.google_api_key_backup.strip(),
                "base_url": settings.google_gemini_base_url.rstrip("/"),
                "models": list(g_models),
                "source": "env",
            }
        if settings.openai_api_key.strip():
            self.credentials["openai-primary"] = {
                "name": "openai-primary",
                "provider": "openai",
                "tier": "paid",
                "purpose": "backup",
                "description": "OpenAI ChatGPT API key (.env)",
                "api_key": settings.openai_api_key.strip(),
                "base_url": settings.openai_base_url.rstrip("/"),
                "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
                "source": "env",
            }
        if settings.anthropic_api_key.strip():
            self.credentials["anthropic-primary"] = {
                "name": "anthropic-primary",
                "provider": "anthropic",
                "tier": "paid",
                "purpose": "backup",
                "description": "Anthropic Claude API key (.env)",
                "api_key": settings.anthropic_api_key.strip(),
                "base_url": settings.anthropic_base_url.rstrip("/"),
                "models": ["claude-sonnet-4-5", "claude-3-5-haiku-latest", "claude-opus-4-5"],
                "source": "env",
            }
        if settings.ollama_base_url.strip():
            self.credentials["ollama-local"] = {
                "name": "ollama-local",
                "provider": "local",
                "tier": "local",
                "purpose": "local",
                "description": "Local Ollama / OpenAI-compatible server",
                "api_key": "",
                "base_url": settings.ollama_base_url.rstrip("/"),
                "models": [settings.ollama_model or "gemma4:e2b", "qwen2.5-coder:7b", "llama3.2:3b"],
                "source": "env",
            }

    def _load_persisted_state(self) -> None:
        """Load runtime profile & credential overrides if persisted on disk (decrypting API keys)."""
        import os
        from penpot_mcp.services.crypto import transform_credentials_keys

        if not os.path.exists(self._config_path):
            return
        try:
            with open(self._config_path, encoding="utf-8") as fh:
                data = json.load(fh)
            decrypted_creds = transform_credentials_keys(data.get("credentials") or {}, mode="decrypt")
            for name, cred in decrypted_creds.items():
                if isinstance(cred, dict) and cred.get("provider"):
                    self.credentials[name] = cred
            if isinstance(data.get("active_profile"), dict):
                self.active_profile.update(data["active_profile"])
            if isinstance(data.get("agent_instructions"), dict):
                self.agent_instructions.update(data["agent_instructions"])
            if isinstance(data.get("active_user"), dict):
                self.active_user.update(data["active_user"])
            if isinstance(data.get("user_profiles_cache"), dict):
                for uid_key, uentry in data["user_profiles_cache"].items():
                    if isinstance(uentry, dict) and isinstance(uentry.get("credentials"), dict):
                        uentry["credentials"] = transform_credentials_keys(
                            uentry["credentials"], mode="decrypt"
                        )
                self.user_profiles_cache.update(data["user_profiles_cache"])
        except Exception as exc:
            logger.debug("Could not load persisted gateway config: %s", exc)

    def _save_persisted_state(self) -> None:
        """Persist runtime profiles & encrypted credentials with 0600 permissions."""
        import copy
        import os
        from penpot_mcp.services.crypto import transform_credentials_keys

        uid = str(self.active_user.get("id") or "").strip()
        if uid:
            self.user_profiles_cache[uid] = {
                "email": self.active_user.get("email", ""),
                "fullname": self.active_user.get("fullname", ""),
                "active_profile": copy.deepcopy(self.active_profile),
                "credentials": copy.deepcopy(self.credentials),
                "agent_instructions": copy.deepcopy(self.agent_instructions),
            }
        try:
            encrypted_cache: dict[str, dict[str, Any]] = {}
            for uid_key, uentry in self.user_profiles_cache.items():
                entry_copy = copy.deepcopy(uentry)
                if isinstance(entry_copy.get("credentials"), dict):
                    entry_copy["credentials"] = transform_credentials_keys(
                        entry_copy["credentials"], mode="encrypt"
                    )
                encrypted_cache[uid_key] = entry_copy

            payload = {
                "credentials": transform_credentials_keys(self.credentials, mode="encrypt"),
                "active_profile": self.active_profile,
                "agent_instructions": self.agent_instructions,
                "active_user": self.active_user,
                "user_profiles_cache": encrypted_cache,
            }
            with open(self._config_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.chmod(self._config_path, 0o600)
        except Exception as exc:
            logger.debug("Could not persist gateway config: %s", exc)

    async def sync_active_user_to_db(
        self, file_id: str | None = None, page_id: str | None = None
    ) -> bool:
        """Synchronize the active Penpot user's AI profile & keys into public.mcp_user_ai_profile."""
        from penpot_mcp.services.db import db

        uid = str(self.active_user.get("id") or "").strip()
        if not uid:
            return False
        return await db.save_user_ai_state(
            profile_id=uid,
            email=str(self.active_user.get("email") or ""),
            fullname=str(self.active_user.get("fullname") or ""),
            active_profile=dict(self.active_profile),
            credentials=dict(self.credentials),
            agent_instructions=dict(self.agent_instructions),
            last_file_id=file_id,
            last_page_id=page_id,
        )

    async def switch_or_resolve_user(
        self,
        *,
        user_id: str | None = None,
        email: str | None = None,
        session_id: str | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the authenticated Penpot user and activate their isolated AI profile & credentials."""
        import copy
        from penpot_mcp.services.db import db

        current_uid = str(self.active_user.get("id") or "").strip()
        resolved = await db.resolve_authenticated_penpot_user(
            user_id=user_id,
            email=email,
            session_id=session_id,
            file_id=file_id,
        )
        if not resolved:
            if user_id or email:
                self.active_user.update(
                    {
                        "id": (user_id or current_uid or "local-user").strip(),
                        "email": (email or self.active_user.get("email") or "anthony_cruz@outlook.com").strip(),
                        "fullname": self.active_user.get("fullname") or "Anthony Cruz",
                        "resolved_via": "fallback_param",
                    }
                )
            return dict(self.active_user)

        new_uid = str(resolved.get("id") or "").strip()
        if current_uid and current_uid != new_uid:
            # Save previous user's state before switching
            self._save_persisted_state()
            await self.sync_active_user_to_db(file_id=file_id)

        self.active_user = {
            "id": new_uid,
            "email": str(resolved.get("email") or ""),
            "fullname": str(resolved.get("fullname") or ""),
            "lang": str(resolved.get("lang") or "es"),
            "theme": str(resolved.get("theme") or "default"),
            "default_team_id": resolved.get("default_team_id"),
            "default_project_id": resolved.get("default_project_id"),
            "resolved_via": str(resolved.get("resolved_via") or "profile"),
        }

        # Load this user's AI profile from PostgreSQL (or in-memory cache), seeding if first login
        db_state = await db.get_user_ai_state(new_uid)
        cached_state = self.user_profiles_cache.get(new_uid)
        target_state = db_state or cached_state

        if target_state and isinstance(target_state.get("credentials"), dict) and target_state["credentials"]:
            self.credentials = copy.deepcopy(target_state["credentials"])
            if isinstance(target_state.get("active_profile"), dict) and target_state["active_profile"]:
                self.active_profile.update(target_state["active_profile"])
            if isinstance(target_state.get("agent_instructions"), dict) and target_state["agent_instructions"]:
                self.agent_instructions.update(target_state["agent_instructions"])
        else:
            # First time this Penpot user activates AI Studio -> persist their initial profile in PostgreSQL
            self._save_persisted_state()
            await self.sync_active_user_to_db(file_id=file_id)

        return dict(self.active_user)

    def list_catalog(self) -> list[dict[str, Any]]:
        """Return the provider credentials catalog with masked keys (write-only security)."""
        out: list[dict[str, Any]] = []
        for name, cred in self.credentials.items():
            raw_key = str(cred.get("api_key") or "")
            out.append(
                {
                    "credential": name,
                    "provider": cred.get("provider", "gemini"),
                    "tier": cred.get("tier", "free"),
                    "purpose": cred.get("purpose", "primary"),
                    "description": cred.get("description", ""),
                    "key_last4": _mask_last4(raw_key),
                    "has_key": bool(raw_key) or cred.get("provider") == "local",
                    "base_url": cred.get("base_url", ""),
                    "models": list(cred.get("models") or []),
                    "source": cred.get("source", "user"),
                    "validated_at": cred.get("validated_at"),
                }
            )
        return out

    def upsert_credential(
        self,
        *,
        name: str,
        provider: str,
        api_key: str | None = None,
        base_url: str | None = None,
        tier: str | None = None,
        purpose: str | None = None,
        description: str | None = None,
        model: str | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Create or replace a provider credential/account in-place (Vanguard Control ProvidersTab)."""
        clean_name = re.sub(r"[^a-zA-Z0-9_.\-]", "-", (name or "").strip().lower()).strip("-")
        if not clean_name:
            raise ValueError("Account name is required (e.g. 'claude-main', 'openai-gpt4o', 'google-primary').")
        prov = (provider or "gemini").strip().lower()
        prov_meta = next((p for p in PROVIDERS_CATALOG if p["id"] == prov), None)
        if prov_meta is None:
            raise ValueError(f"Unsupported provider '{prov}'.")

        existing = self.credentials.get(clean_name, {})
        eff_key = (
            api_key.strip()
            if (api_key is not None and api_key.strip() and api_key.strip() != "KEEP")
            else str(existing.get("api_key") or "")
        )
        if prov != "local" and not eff_key:
            raise ValueError(f"API key is required for provider '{prov}'.")

        eff_base_url = (
            (base_url or "").strip().rstrip("/")
            or str(existing.get("base_url") or "")
            or str(prov_meta["default_base_url"])
        )
        models = list(existing.get("models") or prov_meta["default_models"])
        if model and model.strip() and model.strip() not in models:
            models.insert(0, model.strip())

        import datetime

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        record = {
            "name": clean_name,
            "provider": prov,
            "tier": (tier or existing.get("tier") or ("free" if prov_meta["freeTier"] else "paid")).strip(),
            "purpose": (purpose or existing.get("purpose") or "primary").strip(),
            "description": (
                description.strip()
                if description is not None
                else str(existing.get("description") or f"{prov_meta['label']} profile")
            ),
            "api_key": eff_key,
            "base_url": eff_base_url,
            "models": models,
            "source": "user",
            "validated_at": now_iso,
        }
        self.credentials[clean_name] = record
        # Reset any open circuit breaker for this account so the new/replaced key is tried immediately
        self.breakers.reset(f"account:{clean_name}")

        if activate or record["purpose"] == "primary":
            chosen_model = (model or "").strip() or models[0]
            self.active_profile.update(
                {
                    "preset": "local" if prov == "local" else ("demo-free" if record["tier"] == "free" else "paid"),
                    "account": clean_name,
                    "provider": prov,
                    "model": chosen_model,
                    "fallback_model": models[1] if len(models) > 1 else chosen_model,
                }
            )
            self.serving_state.update(
                {
                    "tier": "primary",
                    "provider": prov,
                    "model": chosen_model,
                }
            )

        self._save_persisted_state()
        return next(c for c in self.list_catalog() if c["credential"] == clean_name)

    def delete_credential(self, name: str) -> dict[str, Any]:
        """Delete a stored provider credential profile."""
        clean_name = (name or "").strip()
        removed = self.credentials.pop(clean_name, None)
        if removed is None:
            raise KeyError(f"Credential '{clean_name}' not found.")
        if self.active_profile.get("account") == clean_name:
            first = next(iter(self.credentials.values()), None)
            if first:
                self.active_profile["account"] = first["name"]
                self.active_profile["provider"] = first["provider"]
                self.active_profile["model"] = (first.get("models") or ["gemini-3.5-flash-lite"])[0]
        self._save_persisted_state()
        return {"status": "ok", "deleted": clean_name}

    async def test_provider_key(
        self,
        *,
        provider: str,
        api_key: str | None = None,
        base_url: str | None = None,
        credential_name: str | None = None,
    ) -> dict[str, Any]:
        """Test a provider API key or local endpoint and return its available generative models."""
        prov = (provider or "gemini").strip().lower()
        prov_meta = next((p for p in PROVIDERS_CATALOG if p["id"] == prov), PROVIDERS_CATALOG[0])
        eff_key = (api_key or "").strip()
        if (not eff_key or eff_key == "KEEP") and credential_name and credential_name in self.credentials:
            eff_key = str(self.credentials[credential_name].get("api_key") or "")
        eff_base = (base_url or "").strip().rstrip("/") or str(prov_meta["default_base_url"])

        if prov != "local" and not eff_key:
            return {"ok": False, "provider": prov, "models": [], "error": "Missing API key."}

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            try:
                if prov == "gemini":
                    resp = await client.get(
                        f"{eff_base}/v1beta/models",
                        headers={"x-goog-api-key": eff_key},
                    )
                    if resp.status_code >= 400:
                        return {
                            "ok": False,
                            "provider": prov,
                            "models": [],
                            "error": f"Gemini HTTP {resp.status_code}",
                        }
                    data = resp.json()
                    models = [
                        m["name"].split("/")[-1]
                        for m in (data.get("models") or [])
                        if "generateContent" in (m.get("supportedGenerationMethods") or [])
                    ]
                    return {
                        "ok": True,
                        "provider": prov,
                        "models": models[:15] or list(prov_meta["default_models"]),
                    }

                if prov in ("openai", "deepseek", "openai_compatible", "local"):
                    headers = {"Authorization": f"Bearer {eff_key}"} if eff_key else {}
                    resp = await client.get(f"{eff_base}/models", headers=headers)
                    if resp.status_code >= 400:
                        return {
                            "ok": False,
                            "provider": prov,
                            "models": [],
                            "error": f"{prov} HTTP {resp.status_code}",
                        }
                    data = resp.json()
                    models = [
                        str(m.get("id"))
                        for m in (data.get("data") or [])
                        if isinstance(m, dict) and m.get("id")
                    ]
                    return {
                        "ok": True,
                        "provider": prov,
                        "models": models[:20] or list(prov_meta["default_models"]),
                    }

                if prov == "anthropic":
                    resp = await client.get(
                        f"{eff_base}/models",
                        headers={
                            "x-api-key": eff_key,
                            "anthropic-version": "2023-06-01",
                        },
                    )
                    if resp.status_code >= 400:
                        return {
                            "ok": False,
                            "provider": prov,
                            "models": [],
                            "error": f"Anthropic HTTP {resp.status_code}",
                        }
                    data = resp.json()
                    models = [
                        str(m.get("id"))
                        for m in (data.get("data") or [])
                        if isinstance(m, dict) and m.get("id")
                    ]
                    return {
                        "ok": True,
                        "provider": prov,
                        "models": models[:15] or list(prov_meta["default_models"]),
                    }
            except Exception as exc:
                return {
                    "ok": False,
                    "provider": prov,
                    "models": list(prov_meta["default_models"]),
                    "error": f"{type(exc).__name__}: {exc}",
                }

        return {"ok": True, "provider": prov, "models": list(prov_meta["default_models"])}

    def update_active_profile(
        self,
        *,
        preset: str | None = None,
        account: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        rpm: int | None = None,
    ) -> dict[str, Any]:
        """Switch active agent preset, provider account, and model on the fly without restart."""
        if preset is not None and preset.strip():
            clean_preset = preset.strip().lower()
            self.active_profile["preset"] = clean_preset
            if clean_preset == "offline":
                self.serving_state.update(
                    {"tier": "deterministic", "provider": "offline", "model": "sdd-code-mode-v1"}
                )
            elif clean_preset == "local" and "ollama-local" in self.credentials:
                self.active_profile["account"] = "ollama-local"
                self.active_profile["provider"] = "local"
                self.active_profile["model"] = (
                    self.credentials["ollama-local"].get("models") or ["gemma4:e2b"]
                )[0]

        if account is not None and account.strip():
            acc_name = account.strip()
            self.active_profile["account"] = acc_name
            cred = self.credentials.get(acc_name)
            if cred:
                self.active_profile["provider"] = cred.get("provider", "gemini")
                if not model:
                    self.active_profile["model"] = (cred.get("models") or ["gemini-3.5-flash-lite"])[0]

        if provider is not None and provider.strip():
            self.active_profile["provider"] = provider.strip().lower()

        if model is not None and model.strip():
            self.active_profile["model"] = model.strip()

        if fallback_model is not None:
            self.active_profile["fallback_model"] = fallback_model.strip()

        if rpm is not None and int(rpm) > 0:
            self.active_profile["rpm"] = int(rpm)

        if self.active_profile.get("preset") != "offline":
            self.serving_state.update(
                {
                    "tier": "primary",
                    "provider": self.active_profile.get("provider", "gemini"),
                    "model": self.active_profile.get("model", settings.penpot_agent_model),
                }
            )
        self._save_persisted_state()
        return dict(self.active_profile)

    def update_agent_instructions(
        self,
        *,
        op: str = "save",
        addendum: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        """Save or reset the versioned agent instruction addendum (Vanguard Control AgentsTab)."""
        import datetime

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        next_ver = int(self.agent_instructions.get("version", 1)) + 1
        clean_op = (op or "save").strip().lower()
        new_addendum = "" if clean_op == "reset" else addendum.strip()
        clean_note = (note or "").strip() or (
            "Reset to base instruction" if clean_op == "reset" else f"Updated addendum v{next_ver}"
        )
        history = list(self.agent_instructions.get("history") or [])
        history.insert(
            0,
            {
                "version": next_ver,
                "kind": "reset" if clean_op == "reset" else "save",
                "addendum": new_addendum,
                "note": clean_note,
                "created_at": now_iso,
            },
        )
        self.agent_instructions.update(
            {
                "addendum": new_addendum,
                "version": next_ver,
                "note": clean_note,
                "updated_at": now_iso,
                "history": history[:20],
            }
        )
        self._save_persisted_state()
        return dict(self.agent_instructions)

    def get_config(self) -> dict[str, Any]:
        """Return complete AI Gateway configuration for the Mini-Chat Config Drawer."""
        status = self.get_status()
        return {
            "status": "ok",
            "active_user": dict(self.active_user),
            "active_profile": dict(self.active_profile),
            "presets": PRESETS_CATALOG,
            "providers_catalog": PROVIDERS_CATALOG,
            "credentials": self.list_catalog(),
            "agent_instructions": {
                "base": self.agent_instructions.get("base", BASE_AGENT_INSTRUCTION),
                "addendum": self.agent_instructions.get("addendum", ""),
                "version": self.agent_instructions.get("version", 1),
                "note": self.agent_instructions.get("note", ""),
                "updated_at": self.agent_instructions.get("updated_at"),
                "history": self.agent_instructions.get("history", []),
            },
            "routes": status["routes"],
            "serving": status["serving"],
            "breakers": status["breakers"],
        }

    def build_route_chain(self) -> list[ProviderRoute]:
        """Construct the ordered failover chain from active profile + stored credentials."""
        preset = (self.active_profile.get("preset") or settings.penpot_agent_mode or "auto").strip().lower()
        primary_acc = (self.active_profile.get("account") or "google-primary").strip()
        primary_model = (
            self.active_profile.get("model") or settings.penpot_agent_model or "gemini-3.5-flash-lite"
        ).strip()
        fallback_model = (
            self.active_profile.get("fallback_model") or settings.penpot_agent_fallback_model or ""
        ).strip()

        routes: list[ProviderRoute] = []
        if preset not in ("deterministic", "offline"):
            # 1. Place the selected active account first as 'primary'
            active_cred = self.credentials.get(primary_acc)
            if active_cred and (active_cred.get("api_key") or active_cred.get("provider") == "local"):
                if preset != "local" or active_cred.get("provider") == "local":
                    p_models = [primary_model]
                    if fallback_model and fallback_model != primary_model:
                        p_models.append(fallback_model)
                    routes.append(
                        ProviderRoute(
                            tier="primary",
                            provider=str(active_cred.get("provider") or "gemini"),
                            account=primary_acc,
                            models=p_models,
                            api_key=str(active_cred.get("api_key") or ""),
                            base_url=str(active_cred.get("base_url") or "").rstrip("/"),
                            cloud=active_cred.get("provider") != "local",
                        )
                    )

            # 2. Append remaining configured accounts as ordered backups
            for acc_name, cred in self.credentials.items():
                if acc_name == primary_acc:
                    continue
                prov = str(cred.get("provider") or "gemini")
                if preset == "local" and prov != "local":
                    continue
                if preset != "local" and prov == "local":
                    continue
                if not cred.get("api_key") and prov != "local":
                    continue
                c_models = list(cred.get("models") or [primary_model])
                if prov == self.active_profile.get("provider") and primary_model not in c_models:
                    c_models.insert(0, primary_model)
                routes.append(
                    ProviderRoute(
                        tier="local" if prov == "local" else "backup",
                        provider=prov,
                        account=acc_name,
                        models=c_models[:2],
                        api_key=str(cred.get("api_key") or ""),
                        base_url=str(cred.get("base_url") or "").rstrip("/"),
                        cloud=prov != "local",
                    )
                )

            # 3. If local Ollama is configured and not yet in the chain, add it as local fallback before offline
            if preset != "local" and "ollama-local" in self.credentials:
                loc = self.credentials["ollama-local"]
                routes.append(
                    ProviderRoute(
                        tier="local",
                        provider="local",
                        account="ollama-local",
                        models=list(loc.get("models") or ["gemma4:e2b"])[:1],
                        base_url=str(loc.get("base_url") or "http://host.docker.internal:11434/v1").rstrip("/"),
                        cloud=False,
                    )
                )

        routes.append(
            ProviderRoute(
                tier="deterministic",
                provider="offline",
                account="sdd-deterministic",
                models=["sdd-code-mode-v1"],
                cloud=False,
            )
        )
        return routes

    def get_status(self) -> dict[str, Any]:
        """Return safe, non-secret status of the AI Gateway, Plugin WS, and Circuit Breakers."""
        from penpot_mcp.services.db import db

        routes = self.build_route_chain()
        live_ctx = gateway.active_context
        return {
            "status": "ok",
            "active_user": dict(self.active_user),
            "agent_mode": self.active_profile.get("preset", settings.penpot_agent_mode),
            "active_account": self.active_profile.get("account", "google-primary"),
            "active_provider": self.active_profile.get("provider", "gemini"),
            "default_model": self.active_profile.get("model", settings.penpot_agent_model),
            "fallback_model": self.active_profile.get("fallback_model", settings.penpot_agent_fallback_model),
            "rpm": self.active_profile.get("rpm", settings.penpot_agent_rpm),
            "addendum_version": self.agent_instructions.get("version", 1),
            "has_addendum": bool(self.agent_instructions.get("addendum")),
            "has_primary_key": any(
                c.get("provider") == "gemini" and c.get("has_key") for c in self.list_catalog()
            ),
            "has_backup_key": len([c for c in self.list_catalog() if c.get("has_key")]) > 1,
            "has_openai_key": any(
                c.get("provider") in ("openai", "deepseek", "openai_compatible") and c.get("has_key")
                for c in self.list_catalog()
            ),
            "has_anthropic_key": any(
                c.get("provider") == "anthropic" and c.get("has_key") for c in self.list_catalog()
            ),
            "credentials_count": len(self.credentials),
            "valkey_cache": db.cache.stats(),
            "routes": [
                {
                    "tier": r.tier,
                    "provider": r.provider,
                    "account": r.account,
                    "models": r.models,
                    "cloud": r.cloud,
                }
                for r in routes
            ],
            "serving": dict(self.serving_state),
            "breakers": self.breakers.active_list(),
            "plugin": live_ctx,
        }

    async def resolve_target_canvas(
        self, file_id: str | None = None, page_id: str | None = None
    ) -> dict[str, Any]:
        """Resolve active file_id and page_id from request, live plugin context, or Penpot DB."""
        ctx = gateway.active_context
        eff_file_id = (file_id or ctx.get("file_id") or "").strip() or None
        eff_page_id = (page_id or ctx.get("page_id") or "").strip() or None
        file_name = ctx.get("file_name")
        page_name = ctx.get("page_name")

        from penpot_mcp.tools import files as _files_mod
        from penpot_mcp.tools import projects as _proj_mod

        try:
            if not eff_file_id:
                projs = await _proj_mod.list_projects()
                if isinstance(projs, list) and projs:
                    for proj in projs:
                        pid = proj.get("id")
                        if not pid:
                            continue
                        flist = await _proj_mod.list_files(str(pid))
                        if isinstance(flist, list) and flist:
                            eff_file_id = str(flist[0]["id"])
                            file_name = str(flist[0].get("name") or "Design File")
                            break

            if eff_file_id and not eff_page_id:
                pages_res = await _files_mod.get_file_pages(eff_file_id)
                pages_list = (
                    pages_res.get("pages", [])
                    if isinstance(pages_res, dict)
                    else pages_res
                    if isinstance(pages_res, list)
                    else []
                )
                if pages_list:
                    eff_page_id = str(pages_list[0]["id"])
                    page_name = str(pages_list[0].get("name") or "Page 1")
        except Exception as exc:
            logger.debug("Canvas auto-discovery fallback warning: %s", exc)

        return {
            "file_id": eff_file_id,
            "file_name": file_name,
            "page_id": eff_page_id,
            "page_name": page_name,
            "selected_shape_ids": ctx.get("selected_shape_ids", []),
            "selection": ctx.get("selection", []),
            "connected": ctx.get("connected", False),
        }

    async def _call_provider_route(
        self,
        client: httpx.AsyncClient,
        route: ProviderRoute,
        model: str,
        gemini_body: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], Optional[RouteFailure]]:
        """Invoke a single provider route using the Gemini REST protocol or translated schema."""
        if route.cloud:
            eff_rpm = int(self.active_profile.get("rpm") or settings.penpot_agent_rpm or 15)
            await self.rpm_limiter.acquire(f"{route.account}:{model}", eff_rpm)

        if route.provider == "gemini":
            body_fixed, _ = backfill_thought_signatures(gemini_body)
            url = f"{route.base_url}/v1beta/models/{model}:generateContent"
            headers = {
                "x-goog-api-key": route.api_key,
                "content-type": "application/json",
            }
            try:
                resp = await client.post(url, json=body_fixed, headers=headers)
            except httpx.TransportError as exc:
                return None, classify_failure(None, exc=exc)
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            if resp.status_code >= 400:
                return None, classify_failure(resp.status_code, payload, resp.headers)

            for _ in range(EMPTY_RETRIES):
                if not is_empty_gemini_answer(payload):
                    break
                try:
                    retry_resp = await client.post(url, json=body_fixed, headers=headers)
                    if retry_resp.status_code < 400:
                        payload = retry_resp.json()
                except Exception:
                    break
            return payload, None

        if route.provider in ("openai", "deepseek", "openai_compatible", "local"):
            url = f"{route.base_url}/chat/completions"
            headers = {"content-type": "application/json"}
            if route.api_key:
                headers["Authorization"] = f"Bearer {route.api_key}"
            translated = gemini_to_openai(gemini_body, model)
            try:
                resp = await client.post(url, json=translated, headers=headers)
            except httpx.TransportError as exc:
                return None, classify_failure(None, exc=exc)
            try:
                raw = resp.json()
            except ValueError:
                raw = None
            if resp.status_code >= 400:
                return None, classify_failure(resp.status_code, raw, resp.headers)
            return openai_to_gemini(raw or {}), None

        if route.provider == "anthropic":
            url = f"{route.base_url}/messages"
            headers = {
                "x-api-key": route.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            translated = gemini_to_anthropic(gemini_body, model)
            try:
                resp = await client.post(url, json=translated, headers=headers)
            except httpx.TransportError as exc:
                return None, classify_failure(None, exc=exc)
            try:
                raw = resp.json()
            except ValueError:
                raw = None
            if resp.status_code >= 400:
                return None, classify_failure(resp.status_code, raw, resp.headers)
            return anthropic_to_gemini(raw or {}), None

        return None, RouteFailure("client", f"Unsupported provider {route.provider}")

    async def _generate_with_failover(
        self, gemini_body: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], ProviderRoute, str, list[str]]:
        """Walk the failover chain across configured routes until a model answers or deterministic takes over."""
        routes = self.build_route_chain()
        reasons: list[str] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0)) as client:
            for route in routes:
                if route.provider == "offline":
                    return None, route, route.models[0], reasons
                for model in route.models:
                    blocked = self.breakers.blocking(route.account, route.provider, model)
                    if blocked:
                        reasons.append(f"{route.account}/{model} skipped ({blocked['kind']})")
                        continue
                    payload, failure = await self._call_provider_route(
                        client, route, model, gemini_body
                    )
                    if failure is None and payload is not None:
                        self.serving_state.update(
                            {
                                "tier": route.tier,
                                "provider": route.provider,
                                "model": model,
                                "calls_total": int(self.serving_state.get("calls_total", 0)) + 1,
                            }
                        )
                        return payload, route, model, reasons
                    if failure is not None:
                        reasons.append(f"{route.account}/{model}: {failure.detail}")
                        if failure.kind in FAILOVER_KINDS:
                            self.breakers.open_for_failure(
                                route.account, route.provider, model, failure
                            )

        det_route = routes[-1]
        return None, det_route, det_route.models[0], reasons

    async def _execute_agent_tool_call(
        self,
        fn_name: str,
        fn_args: dict[str, Any],
        canvas_ctx: dict[str, Any],
        tool_trace: list[dict[str, Any]],
        created_shapes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a tool call requested by the LLM (or deterministic planner) and record trace."""
        from penpot_mcp.tools.subtools import _build_batch_dispatch, playbook_run

        started = time.monotonic()
        file_id = canvas_ctx.get("file_id")
        page_id = canvas_ctx.get("page_id")

        if fn_name == "create_ui_artifact":
            if not file_id or not page_id:
                return {"status": "ERROR", "error": "No active file_id/page_id available."}
            template_kind = str(fn_args.get("template_kind") or "hero_card")
            rec_origin = canvas_ctx.get("recommended_free_origin") or {"x": 80.0, "y": 80.0}
            default_x = float(rec_origin.get("x", 80.0))
            default_y = float(rec_origin.get("y", 80.0))
            raw_x = fn_args.get("x")
            raw_y = fn_args.get("y")
            eff_x = float(raw_x) if raw_x is not None and float(raw_x) != 80.0 else default_x
            eff_y = float(raw_y) if raw_y is not None and float(raw_y) != 80.0 else default_y
            batch_name, steps = build_ui_template_steps(
                template_kind=template_kind,
                file_id=file_id,
                page_id=page_id,
                title=fn_args.get("title"),
                subtitle=fn_args.get("subtitle"),
                cta_text=fn_args.get("cta_text"),
                accent_color=str(fn_args.get("accent_color") or "#6366F1"),
                x=eff_x,
                y=eff_y,
            )
            raw_str = await playbook_run(
                action="code",
                name=batch_name,
                steps=steps,
                return_only="summary",
                verbosity="summary",
            )
            res = json.loads(raw_str)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            for step_item in res.get("steps_summary") or res.get("results") or []:
                out = step_item.get("output") or {}
                sid = step_item.get("shape_id") or out.get("shape_id") or out.get("id")
                if sid and step_item.get("ok"):
                    created_shapes.append(
                        {
                            "shape_id": sid,
                            "step_id": step_item.get("id"),
                            "name": (step_item.get("name") or out.get("name") or step_item.get("id")),
                        }
                    )
            tool_trace.append(
                {
                    "tool": "playbook_run",
                    "action": f"code ({batch_name})",
                    "status": res.get("status", "OK"),
                    "steps_run": res.get("steps_run", len(steps)),
                    "duration_ms": duration_ms,
                }
            )
            return res

        if fn_name == "run_penpot_batch":
            steps = fn_args.get("steps") or []
            batch_name = str(fn_args.get("name") or "agent-batch")
            raw_str = await playbook_run(
                action="code",
                name=batch_name,
                steps=steps,
                vars={"file_id": file_id, "page_id": page_id},
                return_only="summary",
                verbosity="summary",
            )
            res = json.loads(raw_str)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            for step_item in res.get("steps_summary") or res.get("results") or []:
                out = step_item.get("output") or {}
                sid = step_item.get("shape_id") or out.get("shape_id") or out.get("id")
                if sid and step_item.get("ok"):
                    created_shapes.append(
                        {
                            "shape_id": sid,
                            "step_id": step_item.get("id"),
                            "name": (step_item.get("name") or out.get("name") or step_item.get("id")),
                        }
                    )
            tool_trace.append(
                {
                    "tool": "playbook_run",
                    "action": f"code ({batch_name})",
                    "status": res.get("status", "OK"),
                    "steps_run": res.get("steps_run", len(steps)),
                    "duration_ms": duration_ms,
                }
            )
            return res

        if fn_name == "call_penpot_branch_tool":
            tool_name = str(fn_args.get("tool") or "discover").strip().lower()
            action_name = str(fn_args.get("action") or "capabilities").strip().lower()
            params = dict(fn_args.get("params") or {})
            params["action"] = action_name
            params.setdefault("verbosity", "summary")
            if file_id and "file_id" not in params:
                params["file_id"] = file_id
            if page_id and "page_id" not in params:
                params["page_id"] = page_id
            dispatch = _build_batch_dispatch()
            fn = dispatch.get(tool_name)
            if fn is None:
                return {"status": "ERROR", "error": f"Unknown branch tool {tool_name!r}"}
            raw_str = await fn(**params)
            res = json.loads(raw_str) if isinstance(raw_str, str) else raw_str
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            sid = res.get("shape_id") or res.get("id") if isinstance(res, dict) else None
            if sid and tool_name == "create":
                created_shapes.append(
                    {"shape_id": sid, "name": params.get("name") or action_name}
                )
            tool_trace.append(
                {
                    "tool": tool_name,
                    "action": action_name,
                    "status": res.get("status", "OK") if isinstance(res, dict) else "OK",
                    "duration_ms": duration_ms,
                }
            )
            return res

        if fn_name == "query_canvas_vision_pgvector":
            from penpot_mcp.services.vision import sync_and_query_canvas_vision

            query_str = str(fn_args.get("query") or "")
            top_k = int(fn_args.get("top_k") or 10)
            vis = await sync_and_query_canvas_vision(
                file_id=file_id,
                page_id=page_id,
                query_text=query_str,
                top_k=top_k,
            )
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            tool_trace.append(
                {
                    "tool": "pgvector_vision",
                    "action": f"semantic_search ({query_str[:24]})",
                    "status": "OK",
                    "duration_ms": duration_ms,
                }
            )
            return vis

        if fn_name == "media_sandbox_ffmpeg":
            ffmpeg_url = os.environ.get("FFMPEG_MCP_URL", "http://penpot-ffmpeg-mcp:8890").rstrip("/")
            mode = str(fn_args.get("mode") or ("pipeline" if fn_args.get("steps") else "call"))
            action = str(fn_args.get("action") or "probe")
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(
                    f"{ffmpeg_url}/api/call",
                    json={
                        "mode": mode,
                        "action": action,
                        "params": fn_args.get("params") or fn_args,
                        "steps": fn_args.get("steps"),
                        "verbosity": fn_args.get("verbosity") or "summary",
                    },
                )
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            res = resp.json() if resp.content else {"status": "ERROR", "error": f"HTTP {resp.status_code}"}
            tool_trace.append(
                {
                    "tool": "ffmpeg_mcp",
                    "action": f"{mode}:{action}" if mode != "pipeline" else "pipeline",
                    "status": res.get("status", "OK" if resp.status_code < 400 else "ERROR"),
                    "duration_ms": duration_ms,
                }
            )
            return res

        if fn_name == "design_suite_sandbox":
            suite_url = os.environ.get("SUITE_MCP_URL", "http://penpot-suite-mcp:8891").rstrip("/")
            mode = str(fn_args.get("mode") or ("pipeline" if fn_args.get("steps") else "call"))
            action = str(fn_args.get("action") or "extract_palette")
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(
                    f"{suite_url}/api/call",
                    json={
                        "mode": mode,
                        "action": action,
                        "params": fn_args.get("params") or fn_args,
                        "steps": fn_args.get("steps"),
                        "verbosity": fn_args.get("verbosity") or "summary",
                    },
                )
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            res = resp.json() if resp.content else {"status": "ERROR", "error": f"HTTP {resp.status_code}"}
            tool_trace.append(
                {
                    "tool": "suite_mcp",
                    "action": f"{mode}:{action}" if mode != "pipeline" else "pipeline",
                    "status": res.get("status", "OK" if resp.status_code < 400 else "ERROR"),
                    "duration_ms": duration_ms,
                }
            )
            return res

        return {"status": "ERROR", "error": f"Unknown agent function {fn_name!r}"}

    async def _run_deterministic_turn(
        self,
        prompt: str,
        canvas_ctx: dict[str, Any],
        tool_trace: list[dict[str, Any]],
        created_shapes: list[dict[str, Any]],
        quick_action: str | None = None,
    ) -> str:
        """Synthesize and execute deterministic SDD operations when in deterministic mode or offline."""
        lower = (quick_action or prompt or "").strip().lower()
        file_id = canvas_ctx.get("file_id")
        page_id = canvas_ctx.get("page_id")

        if any(
            k in lower
            for k in (
                "vision_scan",
                "pgvector",
                "que hay en",
                "qué hay en",
                "que ves",
                "qué ves",
                "ojos",
                "what is on",
            )
        ):
            vis_res = await self._execute_agent_tool_call(
                "query_canvas_vision_pgvector",
                {"query": prompt or "hero card title button frame", "top_k": 10},
                canvas_ctx,
                tool_trace,
                created_shapes,
            )
            return str(vis_res.get("prompt_eye_summary") or json.dumps(vis_res, default=str)[:400])

        if any(k in lower for k in ("bind_tokens", "token", "tokens", "auto_bind")):
            res = await self._execute_agent_tool_call(
                "call_penpot_branch_tool",
                {
                    "tool": "assets",
                    "action": "auto_bind_tokens",
                    "params": {
                        "file_id": file_id,
                        "page_id": page_id,
                        "shape_ids": canvas_ctx.get("selected_shape_ids") or None,
                    },
                },
                canvas_ctx,
                tool_trace,
                created_shapes,
            )
            return (
                f"Auto-bound library design tokens on page `{canvas_ctx.get('page_name') or page_id}`: "
                f"`{json.dumps(res.get('items', res), default=str)[:280]}`"
            )

        if any(k in lower for k in ("inspect_css", "css", "codigo", "code")):
            sel_ids = canvas_ctx.get("selected_shape_ids") or []
            target_id = sel_ids[0] if sel_ids else None
            if not target_id and file_id and page_id:
                objs = await self._execute_agent_tool_call(
                    "call_penpot_branch_tool",
                    {"tool": "shapes", "action": "list", "params": {"file_id": file_id, "page_id": page_id}},
                    canvas_ctx,
                    tool_trace,
                    created_shapes,
                )
                items = objs.get("items") or []
                for it in items:
                    if isinstance(it, dict) and it.get("type") != "frame" and it.get("id"):
                        target_id = str(it["id"])
                        break
                if not target_id and items:
                    target_id = str(items[0].get("id"))
            if target_id:
                css_res = await self._execute_agent_tool_call(
                    "call_penpot_branch_tool",
                    {
                        "tool": "shapes",
                        "action": "css",
                        "params": {"file_id": file_id, "page_id": page_id, "shape_id": target_id},
                    },
                    canvas_ctx,
                    tool_trace,
                    created_shapes,
                )
                return f"CSS generado para `{css_res.get('name', target_id)}`:\n```css\n{css_res.get('css', '')}\n```"

        if any(k in lower for k in ("login", "sign in", "auth", "sesion", "sesión", "formulario")):
            template_kind = "login_form"
        elif any(k in lower for k in ("pricing", "plan", "precio", "precios", "subscription")):
            template_kind = "pricing_card"
        elif any(k in lower for k in ("nav", "navbar", "header", "menu", "barra")):
            template_kind = "navbar"
        else:
            template_kind = "hero_card"

        clean_title = prompt.strip()[:48] if len(prompt.strip()) > 3 else None
        await self._execute_agent_tool_call(
            "create_ui_artifact",
            {
                "template_kind": template_kind,
                "title": clean_title,
            },
            canvas_ctx,
            tool_trace,
            created_shapes,
        )
        return (
            f"Artefacto `{template_kind}` creado en el lienzo de Penpot ({len(created_shapes)} capas generadas "
            f"en 1 turno vía `playbook_run(action='code')`)."
        )

    async def chat(
        self,
        prompt: str,
        *,
        file_id: str | None = None,
        page_id: str | None = None,
        user_id: str | None = None,
        user_email: str | None = None,
        session_id: str | None = None,
        quick_action: str | None = None,
        history: list[dict[str, Any]] | None = None,
        attachment_ids: list[str] | None = None,
        sandbox_persistent: bool | None = None,
    ) -> AgentTurnResult:
        """Run an end-to-end Penpot AI Studio turn with multi-user profile isolation and pgvector Canvas Vision."""
        from penpot_mcp.services.sandbox_sessions import sandbox_sessions
        from penpot_mcp.services.vision import sync_and_query_canvas_vision

        eff_sid = session_id or "default"
        if sandbox_persistent is not None:
            sb_state = await sandbox_sessions.set_policy(eff_sid, bool(sandbox_persistent))
        else:
            sb_state = await sandbox_sessions.get_session(eff_sid)
        turn_artifacts: list[dict[str, Any]] = []

        started = time.monotonic()
        canvas_ctx = await self.resolve_target_canvas(file_id=file_id, page_id=page_id)
        active_user = await self.switch_or_resolve_user(
            user_id=user_id,
            email=user_email,
            session_id=session_id,
            file_id=canvas_ctx.get("file_id"),
        )
        from penpot_mcp.services.db import db

        persisted_chat = await db.get_chat_session(eff_sid)
        prev_revn = persisted_chat.get("last_revn") if persisted_chat else None
        canvas_delta = await db.get_canvas_delta_since(
            str(canvas_ctx.get("file_id") or ""), prev_revn
        )
        vision_ctx = await sync_and_query_canvas_vision(
            file_id=canvas_ctx.get("file_id"),
            page_id=canvas_ctx.get("page_id"),
            query_text=prompt or quick_action or "",
            top_k=10,
        )
        canvas_ctx["recommended_free_origin"] = vision_ctx.get(
            "recommended_free_origin", {"x": 80.0, "y": 80.0}
        )
        canvas_ctx["vision_summary"] = vision_ctx

        tool_trace: list[dict[str, Any]] = []
        created_shapes: list[dict[str, Any]] = []

        # Fast-path for explicit quick_action triggers when deterministic speed is preferred
        if quick_action and quick_action.strip().lower() in (
            "hero_card",
            "login_form",
            "pricing_card",
            "navbar",
            "bind_tokens",
            "inspect_css",
            "vision_scan",
        ):
            reply_text = await self._run_deterministic_turn(
                prompt or quick_action,
                canvas_ctx,
                tool_trace,
                created_shapes,
                quick_action=quick_action,
            )
            if created_shapes and gateway.is_interactive:
                await gateway.execute_intent(
                    "focus_shapes", shape_ids=[s["shape_id"] for s in created_shapes[:1]]
                )
            latency = round((time.monotonic() - started) * 1000, 2)
            self.serving_state["last_latency_ms"] = latency
            return AgentTurnResult(
                status="OK",
                reply=reply_text,
                provider_used="sdd-quick-action",
                model_used=str(self.active_profile.get("model") or settings.penpot_agent_model),
                tier_used="quick-action",
                file_id=canvas_ctx.get("file_id"),
                page_id=canvas_ctx.get("page_id"),
                user_id=active_user.get("id"),
                user_email=active_user.get("email"),
                user_fullname=active_user.get("fullname"),
                vision_summary={
                    "pgvector_active": vision_ctx.get("pgvector_active", False),
                    "total_shapes": vision_ctx.get("total_shapes", 0),
                    "recommended_free_origin": vision_ctx.get("recommended_free_origin"),
                    "top_matches": (vision_ctx.get("matched_shapes") or [])[:4],
                },
                created_shapes=created_shapes,
                tool_trace=tool_trace,
                latency_ms=latency,
            )

        addendum_text = str(self.agent_instructions.get("addendum") or "").strip()
        addendum_block = (
            f"\n\nAdministrator Addendum (v{self.agent_instructions.get('version', 1)}):\n{addendum_text}"
            if addendum_text
            else ""
        )
        eye_block = str(vision_ctx.get("prompt_eye_summary") or "")
        rec_xy = vision_ctx.get("recommended_free_origin") or {"x": 80.0, "y": 80.0}
        att_blocks: list[str] = []
        for aid in (attachment_ids or [])[:5]:
            att = self.attachments_cache.get(str(aid))
            if att and att.get("context_text"):
                sb = f"\n[sandbox_path: {att['sandbox_path']}]" if att.get("sandbox_path") else ""
                att_blocks.append(f"{att['context_text']}{sb}")
        attachments_section = (
            "\n\n=== ATTACHED FILES & TEMPLATES CONTEXT ===\n"
            + "\n---\n".join(att_blocks)
            + "\n(Sandbox tools available: `media_sandbox_ffmpeg` for image/video probe/resize/crop/convert, "
            "`design_suite_sandbox` for svg_optimize/raster_to_svg/extract_palette/wcag_contrast/remove_background/doc_extract/pdf_render_pages/data_sandbox_query.)"
            + "\n==========================================="
            if att_blocks
            else ""
        )
        delta_section = (
            "\n\n=== MANUAL / CANVAS CHANGES SINCE LAST TURN ===\n"
            f"revn {canvas_delta.get('previous_revn')} -> {canvas_delta.get('current_revn')}: "
            + json.dumps(canvas_delta.get("recent_changes") or [], default=str)
            + "\n================================================="
            if canvas_delta.get("changed")
            else ""
        )
        system_prompt = (
            "You are Penpot AI Studio, an expert UI/UX design agent embedded inside Penpot via the "
            "Penpot MCP Hybrid Gateway and SDK Agent Pipeline.\n"
            f"Authenticated Penpot Designer:\n"
            f"- user_id: {active_user.get('id')}\n"
            f"- fullname: {active_user.get('fullname')}\n"
            f"- email: {active_user.get('email')}\n\n"
            f"Active Canvas Context:\n"
            f"- file_id: {canvas_ctx.get('file_id')}\n"
            f"- file_name: {canvas_ctx.get('file_name')}\n"
            f"- page_id: {canvas_ctx.get('page_id')}\n"
            f"- page_name: {canvas_ctx.get('page_name')}\n"
            f"- selected_shape_ids: {json.dumps(canvas_ctx.get('selected_shape_ids', []))}\n"
            f"- selection_details: {json.dumps(canvas_ctx.get('selection', [])[:10])}\n\n"
            f"=== PGVECTOR SEMANTIC & SPATIAL CANVAS VISION EYE ===\n"
            f"{eye_block}\n"
            f"=====================================================\n\n"
            "Rules:\n"
            "1. When the user asks to create a card, hero section, login form, pricing card, navbar, or UI layout, "
            f"call `run_penpot_batch` or `call_penpot_branch_tool` immediately (using free coordinates x={rec_xy.get('x', 80)}, y={rec_xy.get('y', 80)} so you do not overlap existing frames).\n"
            "2. You CAN see the canvas via the PGVECTOR SEMANTIC & SPATIAL CANVAS VISION EYE above! When the user asks what is on the canvas or refers to a specific shape/color/text, use the exact shape_id, coordinates, colors, and texts from the Vision Eye (or call `query_canvas_vision_pgvector` / `call_penpot_branch_tool`).\n"
            "3. INSPECT BEFORE CRITIQUE (Industry Design2Code / ACI Protocol): Whenever the user asks to evaluate, audit, critique, review, or compare the canvas against code/specs, you MUST call `call_penpot_branch_tool` (e.g. `penpot_inspect` with `action='page_objects'` or `'shape_tree'`) BEFORE answering. Never critique from the top-K summary alone: inspect the full page tree to report duplicate/overlapping frames, orphan shapes outside frames, missing UI controls vs. the attached source, spacing/8px grid drift, and border/token mismatches.\n"
            "4. GROUNDED TOOL NAMES & CONCISENESS: Cite only declared tools (`call_penpot_branch_tool`, `run_penpot_batch`, `query_canvas_vision_pgvector`, `media_sandbox_ffmpeg`) and keep your final response concise in the user's language (Spanish or English)."
            f"{delta_section}"
            f"{attachments_section}"
            f"{addendum_block}"
        )

        contents: list[dict[str, Any]] = []
        for item in (history or [])[-6:]:
            if isinstance(item, dict) and item.get("text"):
                role = "model" if item.get("role") in ("assistant", "model") else "user"
                txt = str(item["text"])
                if len(txt) > 1200:
                    txt = txt[:1180] + "... [compacted]"
                contents.append({"role": role, "parts": [{"text": txt}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        fallback_reasons: list[str] = []
        used_route: Optional[ProviderRoute] = None
        used_model = str(self.active_profile.get("model") or settings.penpot_agent_model)
        final_reply = ""

        for _turn in range(max(1, settings.penpot_agent_max_turns)):
            gemini_body: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": contents,
                "tools": [{"functionDeclarations": AGENT_FUNCTION_DECLARATIONS}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
            }
            payload, route, model_name, reasons = await self._generate_with_failover(gemini_body)
            fallback_reasons.extend(r for r in reasons if r not in fallback_reasons)
            used_route, used_model = route, model_name

            if payload is None or route.provider == "offline":
                final_reply = await self._run_deterministic_turn(
                    prompt, canvas_ctx, tool_trace, created_shapes, quick_action=quick_action
                )
                break

            candidate = ((payload.get("candidates") or [{}])[0]) or {}
            model_content = candidate.get("content") or {"role": "model", "parts": []}
            parts = model_content.get("parts") or []
            fn_calls = [p["functionCall"] for p in parts if isinstance(p, dict) and "functionCall" in p]
            text_parts = [p["text"] for p in parts if isinstance(p, dict) and p.get("text") and not p.get("thought")]

            if not fn_calls:
                joined_text = "\n".join(text_parts).strip()
                if not tool_trace and _ANNOUNCE_RE.search(joined_text[-300:]):
                    contents.append(model_content)
                    contents.append({"role": "user", "parts": [{"text": NUDGE_PROMPT}]})
                    continue
                final_reply = joined_text
                break

            contents.append(model_content)
            response_parts: list[dict[str, Any]] = []
            for fc in fn_calls:
                fname = str(fc.get("name") or "")
                fargs = dict(fc.get("args") or {})
                tool_out = await self._execute_agent_tool_call(
                    fname, fargs, canvas_ctx, tool_trace, created_shapes
                )
                if isinstance(tool_out, dict) and fname in ("media_sandbox_ffmpeg", "design_suite_sandbox"):
                    out_paths: list[str] = []
                    if isinstance(tool_out.get("output_path"), str) and tool_out["output_path"]:
                        out_paths.append(tool_out["output_path"])
                    for p_item in tool_out.get("pages_rendered") or []:
                        if isinstance(p_item, str) and p_item not in out_paths:
                            out_paths.append(p_item)
                    for rel_p in out_paths:
                        art = await sandbox_sessions.register_artifact(eff_sid, rel_p, tool=fname)
                        if all(a.get("path") != art["path"] for a in turn_artifacts):
                            turn_artifacts.append(art)
                response_parts.append(
                    {
                        "functionResponse": {
                            "id": fc.get("id"),
                            "name": fname,
                            "response": tool_out if isinstance(tool_out, dict) else {"result": tool_out},
                        }
                    }
                )
            contents.append({"role": "user", "parts": response_parts})

        # If the LLM finished without calling a creation tool when the prompt clearly requested creating UI,
        # ensure we materialize the artifact deterministically so the canvas is always updated.
        if not tool_trace and any(
            w in prompt.lower()
            for w in ("crea", "create", "build", "make", "genera", "card", "login", "pricing", "navbar", "hero")
        ):
            synth_reply = await self._run_deterministic_turn(
                prompt, canvas_ctx, tool_trace, created_shapes, quick_action=quick_action
            )
            final_reply = f"{final_reply}\n\n{synth_reply}".strip() if final_reply else synth_reply

        if not final_reply:
            final_reply = (
                f"Operación completada ({len(tool_trace)} herramientas ejecutadas, "
                f"{len(created_shapes)} formas creadas/actualizadas en Penpot)."
            )

        if created_shapes and gateway.is_interactive:
            await gateway.execute_intent(
                "focus_shapes", shape_ids=[s["shape_id"] for s in created_shapes[:1]]
            )

        latency = round((time.monotonic() - started) * 1000, 2)
        self.serving_state["last_latency_ms"] = latency
        return AgentTurnResult(
            status="OK",
            reply=final_reply,
            provider_used=used_route.provider if used_route else "offline",
            model_used=used_model,
            tier_used=used_route.tier if used_route else "deterministic",
            file_id=canvas_ctx.get("file_id"),
            page_id=canvas_ctx.get("page_id"),
            user_id=active_user.get("id"),
            user_email=active_user.get("email"),
            user_fullname=active_user.get("fullname"),
            vision_summary={
                "pgvector_active": vision_ctx.get("pgvector_active", False),
                "total_shapes": vision_ctx.get("total_shapes", 0),
                "recommended_free_origin": vision_ctx.get("recommended_free_origin"),
                "top_matches": (vision_ctx.get("matched_shapes") or [])[:4],
            },
            created_shapes=created_shapes,
            tool_trace=tool_trace,
            artifacts=turn_artifacts,
            sandbox_persistent=bool(sb_state.get("persistent")),
            canvas_delta=canvas_delta,
            fallback_reasons=fallback_reasons,
            latency_ms=latency,
        )


# Singleton instance
agent_gateway = PenpotAgentGateway()
