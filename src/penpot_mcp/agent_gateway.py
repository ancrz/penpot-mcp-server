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

    def reset(self) -> int:
        count = len(self._breakers)
        self._breakers.clear()
        return count


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
    created_shapes: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


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

    def build_route_chain(self) -> list[ProviderRoute]:
        """Construct the ordered failover chain from configured .env credentials."""
        mode = (settings.penpot_agent_mode or "auto").strip().lower()
        primary_model = (settings.penpot_agent_model or "gemini-3.5-flash-lite").strip()
        fallback_model = (settings.penpot_agent_fallback_model or "gemini-2.5-flash").strip()
        models = [primary_model] + ([fallback_model] if fallback_model and fallback_model != primary_model else [])

        routes: list[ProviderRoute] = []
        if mode != "deterministic":
            if mode != "local":
                if settings.google_api_key.strip():
                    routes.append(
                        ProviderRoute(
                            tier="primary",
                            provider="gemini",
                            account="google-primary",
                            models=models,
                            api_key=settings.google_api_key.strip(),
                            base_url=settings.google_gemini_base_url.rstrip("/"),
                            cloud=True,
                        )
                    )
                if settings.google_api_key_backup.strip():
                    routes.append(
                        ProviderRoute(
                            tier="backup",
                            provider="gemini",
                            account="google-backup",
                            models=models,
                            api_key=settings.google_api_key_backup.strip(),
                            base_url=settings.google_gemini_base_url.rstrip("/"),
                            cloud=True,
                        )
                    )
                if settings.openai_api_key.strip():
                    routes.append(
                        ProviderRoute(
                            tier="backup-openai",
                            provider="openai",
                            account="openai-primary",
                            models=["gpt-4o-mini"],
                            api_key=settings.openai_api_key.strip(),
                            base_url=settings.openai_base_url.rstrip("/"),
                            cloud=True,
                        )
                    )
                if settings.anthropic_api_key.strip():
                    routes.append(
                        ProviderRoute(
                            tier="backup-anthropic",
                            provider="anthropic",
                            account="anthropic-primary",
                            models=["claude-3-5-haiku-latest"],
                            api_key=settings.anthropic_api_key.strip(),
                            base_url=settings.anthropic_base_url.rstrip("/"),
                            cloud=True,
                        )
                    )
            if mode == "local" and settings.ollama_base_url.strip():
                routes.append(
                    ProviderRoute(
                        tier="local",
                        provider="local",
                        account="ollama-local",
                        models=[settings.ollama_model or "gemma4:e2b"],
                        base_url=settings.ollama_base_url.rstrip("/"),
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
        routes = self.build_route_chain()
        live_ctx = gateway.active_context
        return {
            "status": "ok",
            "agent_mode": settings.penpot_agent_mode,
            "default_model": settings.penpot_agent_model,
            "fallback_model": settings.penpot_agent_fallback_model,
            "has_primary_key": bool(settings.google_api_key.strip()),
            "has_backup_key": bool(settings.google_api_key_backup.strip()),
            "has_openai_key": bool(settings.openai_api_key.strip()),
            "has_anthropic_key": bool(settings.anthropic_api_key.strip()),
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
            await self.rpm_limiter.acquire(f"{route.account}:{model}", settings.penpot_agent_rpm)

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

        if route.provider in ("openai", "local"):
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
            batch_name, steps = build_ui_template_steps(
                template_kind=template_kind,
                file_id=file_id,
                page_id=page_id,
                title=fn_args.get("title"),
                subtitle=fn_args.get("subtitle"),
                cta_text=fn_args.get("cta_text"),
                accent_color=str(fn_args.get("accent_color") or "#6366F1"),
                x=float(fn_args.get("x", 80)),
                y=float(fn_args.get("y", 80)),
            )
            raw_str = await playbook_run(
                action="code",
                name=batch_name,
                steps=steps,
                return_only="all",
                verbosity="summary",
            )
            res = json.loads(raw_str)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            for step_item in res.get("results", []):
                out = step_item.get("output") or {}
                sid = out.get("shape_id") or out.get("id")
                if sid and step_item.get("ok"):
                    created_shapes.append(
                        {
                            "shape_id": sid,
                            "step_id": step_item.get("id"),
                            "name": (out.get("name") or step_item.get("id")),
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
                return_only="all",
                verbosity="summary",
            )
            res = json.loads(raw_str)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            for step_item in res.get("results", []):
                out = step_item.get("output") or {}
                sid = out.get("shape_id") or out.get("id")
                if sid and step_item.get("ok"):
                    created_shapes.append(
                        {
                            "shape_id": sid,
                            "step_id": step_item.get("id"),
                            "name": (out.get("name") or step_item.get("id")),
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
        quick_action: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentTurnResult:
        """Run an end-to-end Penpot AI Studio turn with automatic canvas awareness and tool execution."""
        started = time.monotonic()
        canvas_ctx = await self.resolve_target_canvas(file_id=file_id, page_id=page_id)
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
                model_used=settings.penpot_agent_model,
                tier_used="quick-action",
                file_id=canvas_ctx.get("file_id"),
                page_id=canvas_ctx.get("page_id"),
                created_shapes=created_shapes,
                tool_trace=tool_trace,
                latency_ms=latency,
            )

        system_prompt = (
            "You are Penpot AI Studio, an expert UI/UX design agent embedded inside Penpot via the "
            "Penpot MCP Hybrid Gateway and SDK Agent Pipeline.\n"
            f"Active Canvas Context:\n"
            f"- file_id: {canvas_ctx.get('file_id')}\n"
            f"- file_name: {canvas_ctx.get('file_name')}\n"
            f"- page_id: {canvas_ctx.get('page_id')}\n"
            f"- page_name: {canvas_ctx.get('page_name')}\n"
            f"- selected_shape_ids: {json.dumps(canvas_ctx.get('selected_shape_ids', []))}\n"
            f"- selection_details: {json.dumps(canvas_ctx.get('selection', [])[:10])}\n\n"
            "Rules:\n"
            "1. When the user asks to create a card, hero section, login form, pricing card, navbar, or UI layout, "
            "call `create_ui_artifact` or `run_penpot_batch` immediately to materialize it on the active page.\n"
            "2. When modifying selected shapes, inspecting CSS/SVG, or binding tokens, call `call_penpot_branch_tool`.\n"
            "3. Keep your final response concise and in the user's language (Spanish or English)."
        )

        contents: list[dict[str, Any]] = []
        for item in (history or [])[-6:]:
            if isinstance(item, dict) and item.get("text"):
                role = "model" if item.get("role") in ("assistant", "model") else "user"
                contents.append({"role": role, "parts": [{"text": str(item["text"])}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        fallback_reasons: list[str] = []
        used_route: Optional[ProviderRoute] = None
        used_model = settings.penpot_agent_model
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
            created_shapes=created_shapes,
            tool_trace=tool_trace,
            fallback_reasons=fallback_reasons,
            latency_ms=latency,
        )


# Singleton instance
agent_gateway = PenpotAgentGateway()
