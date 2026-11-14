"""AEP MCP Optimization Engine for Penpot MCP Server (RE: agent-execution-plane).

Provides pure-stdlib, zero-cycle primitives adapted from Agent Execution Plane (AEP):
  - RequestTelemetry & attach_envelope: per-request latency/token/RSS/FD attribution,
    compact verbosity modes ('summary' | 'detail' | 'brief' | 'terse'), and payload clamping.
  - SessionPool (GLOBAL_SESSION_POOL): thread-safe shared-memory (/dev/shm/penpot_mcp_pool)
    warm session slots and key-value state caching (`store_as`, `session_id`).
  - resolve_step_refs & hydrate_struct: native-type-preserving variable piping
    (`$prev.*`, `$steps.<id>.*`, `$cache.<key>.*`, `$slot.<key>.*`, `${var}`).
  - project_fields: server-side dot-path field projection (`select_fields`) with
    token-savings estimation.
  - ToposGuard: DAG cycle detection and Kahn topological ordering for `playbook_run`
    ephemeral batch / code mode.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any
import uuid

MAX_SAFE_JSON_BYTES = 180_000
DEFAULT_MAX_SLOTS = 16
DEFAULT_LEASE_TTL_SEC = 120.0


# ═══════════════════════════════════════════════════════════════
# 1. Per-Request Telemetry & Normalized Agent-Brief Envelope
# ═══════════════════════════════════════════════════════════════


def _self_resources() -> tuple[int, int]:
    """Return (rss_kb, fd_count) for the current process in <0.1ms."""
    rss_kb = 0
    fd_count = 0
    with contextlib.suppress(OSError, ValueError):
        status_path = Path("/proc/self/status")
        if status_path.exists():
            for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split(":", 1)[1].strip().split()
                    if parts:
                        rss_kb = int(parts[0])
                    break
    with contextlib.suppress(OSError):
        fd_count = len(os.listdir("/proc/self/fd"))
    return rss_kb, fd_count


_STARTUP_RSS_KB, _STARTUP_FD_COUNT = _self_resources()


class RequestTelemetry:
    """Measures wall-clock latency, RSS delta, FD delta, serialized bytes, and estimated tokens."""

    def __init__(self, tool_name: str, action: str = "default") -> None:
        self.request_id = f"req_{uuid.uuid4().hex[:10]}"
        self.tool_name = tool_name
        self.action = action
        self.start_ns = time.perf_counter_ns()
        self.rss_before_kb, self.fd_before = _self_resources()

    def finish(self, serialized_bytes: int) -> dict[str, Any]:
        elapsed_ms = round((time.perf_counter_ns() - self.start_ns) / 1_000_000, 2)
        rss_after_kb, fd_after = _self_resources()
        return {
            "request_id": self.request_id,
            "tool": self.tool_name,
            "action": self.action,
            "latency_ms": elapsed_ms,
            "output_bytes": serialized_bytes,
            "estimated_tokens": max(1, round(serialized_bytes / 4)),
            "rss_kb": rss_after_kb,
            "rss_delta_kb": rss_after_kb - self.rss_before_kb,
            "rss_since_startup_kb": rss_after_kb - _STARTUP_RSS_KB,
            "fd_count": fd_after,
            "fd_delta": fd_after - self.fd_before,
        }


def infer_severity(status: str) -> str:
    st = (status or "OK").upper()
    if st in (
        "OK",
        "SUCCESS",
        "APPROVED",
        "CACHE_HIT",
        "CREATED",
        "DRY_RUN",
        "PRUNED",
    ):
        return "INFO"
    if st in ("DEGRADED", "PARTIAL", "WARN", "CACHE_MISS"):
        return "WARN"
    return "ERROR"


def _clamp_oversized_payload(payload: dict[str, Any], max_bytes: int = MAX_SAFE_JSON_BYTES) -> bool:
    """Prevent MCP JSON-RPC buffer overflows by clamping huge nested lists/strings in-place."""
    clamped = False
    for k, v in list(payload.items()):
        if k in ("envelope", "telemetry", "t", "data_base64"):
            continue
        if isinstance(v, str) and len(v) > 32_000:
            payload[k] = v[:32_000]
            payload[f"{k}_clamped"] = True
            clamped = True
        elif isinstance(v, list) and len(v) > 50:
            payload[f"{k}_total_count"] = len(v)
            payload[k] = v[:50]
            clamped = True
        elif isinstance(v, dict) and len(json.dumps(v, ensure_ascii=False, default=str)) > max_bytes // 2:
            items = list(v.items())
            if len(items) > 50:
                payload[f"{k}_total_keys"] = len(items)
                payload[k] = dict(items[:50])
                clamped = True
    return clamped


def normalize_tool_result(raw: Any, action: str = "default") -> dict[str, Any]:
    """Normalize a raw tool return value (dict, list, or primitive) into a standard dict payload."""
    if isinstance(raw, dict):
        out = dict(raw)
        if "error" in out and "status" not in out:
            out["status"] = "ERROR"
        else:
            out.setdefault("status", "OK")
        out.setdefault("action", action)
        return out
    if isinstance(raw, list):
        if len(raw) == 1 and isinstance(raw[0], dict) and "error" in raw[0]:
            return {"status": "ERROR", "action": action, "error": raw[0]["error"], "items": raw}
        return {
            "status": "OK",
            "action": action,
            "count": len(raw),
            "items": raw,
        }
    if raw is None:
        return {"status": "NOT_FOUND", "action": action, "error": "Resource not found"}
    return {"status": "OK", "action": action, "result": raw}


def attach_envelope(
    payload: dict[str, Any],
    tracker: RequestTelemetry,
    *,
    verbosity: str = "summary",
    select_fields: list[str] | None = None,
    severity: str | None = None,
    summary: str | None = None,
    next_action: str | None = None,
    truncated: bool | None = None,
    cursor: str | None = None,
    telemetry: bool | None = None,
) -> str:
    """Attach telemetry, field projection, and normalized agent-brief envelope fields."""
    mode = (verbosity or "summary").strip().lower()
    status_val = str(payload.get("status") or payload.get("verdict") or "OK")
    sev = str(severity or payload.get("severity") or infer_severity(status_val))

    if mode in ("summary", "brief", "terse", "cached") and isinstance(payload.get("items"), list):
        items = payload["items"]
        limit_val = int(payload.get("_limit", 25))
        if len(items) > limit_val:
            payload["total_count"] = len(items)
            payload["items"] = items[:limit_val]
            truncated = True
            cursor = str(limit_val)
    payload.pop("_limit", None)

    if select_fields and isinstance(select_fields, list):
        full_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        projected = project_fields(payload, select_fields)
        if isinstance(projected, dict):
            projected.setdefault("status", status_val)
            if "error" in payload and "error" not in projected:
                projected["error"] = payload["error"]
            proj_bytes = len(json.dumps(projected, ensure_ascii=False, default=str).encode("utf-8"))
            saved_b = max(0, full_bytes - proj_bytes)
            projected["token_savings_estimate"] = {
                "full_bytes": full_bytes,
                "projected_bytes": proj_bytes,
                "saved_bytes": saved_b,
                "savings_pct": round((saved_b / full_bytes * 100), 2) if full_bytes > 0 else 0.0,
            }
            payload = projected

    if truncated is not None:
        payload["truncated"] = truncated
    elif "truncated" not in payload:
        payload["truncated"] = False

    if cursor is not None or "cursor" not in payload:
        payload["cursor"] = cursor

    if summary is None:
        summary = (
            payload.get("message")
            or payload.get("error")
            or f"{tracker.tool_name}:{tracker.action} completed with status {status_val}"
        )

    if next_action is None:
        if sev == "ERROR":
            next_action = "Inspect 'error' or 'valid_actions' fields and retry with corrected parameters."
        elif payload.get("truncated"):
            next_action = "Pass verbosity='detail' or increase 'limit' to inspect remaining items."
        else:
            next_action = "Proceed with the next step in the design workflow."

    is_compact = mode in ("summary", "brief", "terse", "cached")
    is_unindented = mode in ("brief", "terse", "cached")
    indent_val = None if is_unindented else 2

    payload.setdefault("status", status_val)
    payload["severity"] = sev
    payload.setdefault("summary", summary)
    payload.setdefault("next_action", next_action)

    if not is_compact:
        payload["envelope"] = {
            "status": status_val,
            "severity": sev,
            "summary": summary,
            "truncated": bool(payload.get("truncated", False)),
            "cursor": payload.get("cursor"),
            "next_action": next_action,
        }
    else:
        payload.pop("envelope", None)

    provisional = json.dumps(payload, indent=indent_val, ensure_ascii=False, default=str)
    if len(provisional.encode("utf-8")) > MAX_SAFE_JSON_BYTES and _clamp_oversized_payload(payload):
        payload["truncated"] = True
        provisional = json.dumps(payload, indent=indent_val, ensure_ascii=False, default=str)

    prov_bytes = len(provisional.encode("utf-8"))
    elapsed_ms = round((time.perf_counter_ns() - tracker.start_ns) / 1_000_000, 2)
    estimated_tokens = max(1, round(prov_bytes / 4))

    if is_compact:
        if telemetry is True:
            payload["telemetry"] = tracker.finish(prov_bytes)
            payload.pop("t", None)
        elif telemetry is False:
            payload.pop("telemetry", None)
            payload.pop("t", None)
        else:
            payload["t"] = {"ms": elapsed_ms, "tok": estimated_tokens}
            payload.pop("telemetry", None)
    else:
        if telemetry is not False:
            payload["telemetry"] = tracker.finish(prov_bytes)
            payload.pop("t", None)
        else:
            payload.pop("telemetry", None)
            payload.pop("t", None)

    return json.dumps(payload, indent=indent_val, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════
# 2. Shared-Memory (/dev/shm) Session Pool & State Cache
# ═══════════════════════════════════════════════════════════════


def resolve_pool_base_dir() -> Path:
    """Resolve the fastest available volatile directory for Penpot MCP session slots.

    Gracefully tolerates read-only container/sandbox filesystems by keeping state in RAM.
    """
    env_dir = os.environ.get("PENPOT_MCP_POOL_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        with contextlib.suppress(OSError):
            p.mkdir(parents=True, exist_ok=True)
        return p

    for candidate_root in (
        Path("/dev/shm"),
        Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR") else None,
        Path("/tmp"),
    ):
        if candidate_root and candidate_root.exists() and os.access(candidate_root, os.W_OK):
            p = candidate_root / "penpot_mcp_pool"
            with contextlib.suppress(OSError):
                p.mkdir(parents=True, exist_ok=True)
                return p

    return Path("/dev/shm/penpot_mcp_pool")


class SessionPool:
    """Thread-safe warm slot leasing and RAM state cache for multi-step Penpot workflows."""

    def __init__(
        self,
        max_slots: int = DEFAULT_MAX_SLOTS,
        lease_ttl_sec: float = DEFAULT_LEASE_TTL_SEC,
        base_dir: Path | str | None = None,
    ) -> None:
        self.max_slots = max(1, int(max_slots))
        self.lease_ttl_sec = float(lease_ttl_sec)
        self.base_dir = Path(base_dir).resolve() if base_dir else resolve_pool_base_dir()
        with contextlib.suppress(OSError):
            self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        self._slots: dict[int, dict[str, Any]] = {}
        for sid in range(self.max_slots):
            scratch = self.base_dir / f"slot_{sid}"
            with contextlib.suppress(OSError):
                scratch.mkdir(parents=True, exist_ok=True)
            self._slots[sid] = {
                "slot_id": sid,
                "session_id": None,
                "agent_id": None,
                "leased_at": 0.0,
                "expires_at": 0.0,
                "reuse_count": 0,
                "scratch_dir": str(scratch),
                "state_cache": {},
                "in_use": False,
            }

        self._session_map: dict[tuple[str, str], int] = {}
        self._shared_session_cache: dict[str, dict[str, Any]] = {}

    def cache_set(self, session_id: str, agent_id: str, key: str, value: Any) -> dict[str, Any]:
        sid_str = str(session_id or "default")
        aid_str = str(agent_id or "default")
        k_str = str(key).strip()
        with self._lock:
            sess_cache = self._shared_session_cache.setdefault(sid_str, {})
            sess_cache[k_str] = value
            slot_map_key = (sid_str, aid_str)
            if slot_map_key in self._session_map:
                slot_id = self._session_map[slot_map_key]
                slot = self._slots[slot_id]
                slot["state_cache"][k_str] = value
                slot["expires_at"] = time.time() + self.lease_ttl_sec
            else:
                with contextlib.suppress(RuntimeError, OSError):
                    slot = self.acquire(sid_str, aid_str)
                    self._slots[slot["slot_id"]]["state_cache"][k_str] = value
            return {
                "status": "OK",
                "session_id": sid_str,
                "agent_id": aid_str,
                "key": k_str,
                "cached_keys": list(sess_cache.keys()),
            }

    def cache_get(
        self,
        session_id: str,
        agent_id: str = "default",
        key: str | None = None,
    ) -> dict[str, Any]:
        sid_str = str(session_id or "default")
        aid_str = str(agent_id or "default")
        with self._lock:
            sess_cache = dict(self._shared_session_cache.get(sid_str, {}))
            slot_map_key = (sid_str, aid_str)
            if slot_map_key in self._session_map:
                slot_id = self._session_map[slot_map_key]
                sess_cache.update(self._slots[slot_id]["state_cache"])
            if key is not None:
                clean_k = str(key).strip()
                val = sess_cache.get(clean_k)
                return {
                    "status": "OK" if clean_k in sess_cache else "NOT_FOUND",
                    "session_id": sid_str,
                    "agent_id": aid_str,
                    "key": clean_k,
                    "value": val,
                    "found": clean_k in sess_cache,
                }
            return {
                "status": "OK",
                "session_id": sid_str,
                "agent_id": aid_str,
                "cache": sess_cache,
                "keys": list(sess_cache.keys()),
                "count": len(sess_cache),
            }

    def acquire(
        self,
        session_id: str,
        agent_id: str = "default",
        ttl_sec: float | None = None,
    ) -> dict[str, Any]:
        sid_str = str(session_id or "default")
        aid_str = str(agent_id or "default")
        key = (sid_str, aid_str)
        effective_ttl = float(ttl_sec or self.lease_ttl_sec)
        now = time.time()

        with self._lock:
            if key in self._session_map:
                slot_id = self._session_map[key]
                slot = self._slots[slot_id]
                slot["expires_at"] = now + effective_ttl
                slot["reuse_count"] += 1
                slot["in_use"] = True
                return self._sanitize_slot(slot)

            self._reap_expired_locked(now)

            available_slot_id: int | None = None
            for sid, slot in self._slots.items():
                if not slot["in_use"] and slot["session_id"] is None:
                    available_slot_id = sid
                    break

            if available_slot_id is None:
                for sid, slot in self._slots.items():
                    if not slot["in_use"]:
                        available_slot_id = sid
                        old_key = (slot["session_id"], slot["agent_id"])
                        self._session_map.pop(old_key, None)
                        break

            if available_slot_id is None:
                available_slot_id = 0

            slot = self._slots[available_slot_id]
            slot["session_id"] = sid_str
            slot["agent_id"] = aid_str
            slot["leased_at"] = now
            slot["expires_at"] = now + effective_ttl
            slot["reuse_count"] = 1
            slot["in_use"] = True
            slot["state_cache"].clear()
            self._session_map[key] = available_slot_id
            return self._sanitize_slot(slot)

    def release(
        self,
        session_id: str,
        agent_id: str = "default",
        clear_state: bool = False,
    ) -> dict[str, Any]:
        sid_str = str(session_id or "default")
        aid_str = str(agent_id or "default")
        key = (sid_str, aid_str)
        with self._lock:
            if key not in self._session_map:
                return {"status": "NOT_FOUND", "session_id": sid_str, "released": False}
            slot_id = self._session_map[key]
            slot = self._slots[slot_id]
            slot["in_use"] = False
            if clear_state:
                slot["state_cache"].clear()
                self._shared_session_cache.pop(sid_str, None)
                self._clear_dir(Path(slot["scratch_dir"]))
                self._session_map.pop(key, None)
                slot["session_id"] = None
                slot["agent_id"] = None
                slot["reuse_count"] = 0
            return {
                "status": "OK",
                "session_id": sid_str,
                "slot_id": slot_id,
                "released": True,
                "reused": slot["reuse_count"],
            }

    def _reap_expired_locked(self, now: float) -> int:
        reaped = 0
        for sid, slot in self._slots.items():
            if slot["session_id"] is not None and slot["expires_at"] > 0 and now >= slot["expires_at"]:
                key = (slot["session_id"], slot["agent_id"])
                self._session_map.pop(key, None)
                slot["in_use"] = False
                slot["session_id"] = None
                slot["agent_id"] = None
                slot["reuse_count"] = 0
                slot["state_cache"].clear()
                self._clear_dir(Path(slot["scratch_dir"]))
                if not any(s.get("session_id") == key[0] and s.get("slot_id") != sid for s in self._slots.values()):
                    self._shared_session_cache.pop(key[0], None)
                reaped += 1
        return reaped

    def flush(self) -> dict[str, Any]:
        with self._lock:
            flushed = sum(1 for s in self._slots.values() if s.get("session_id") is not None)
            self._session_map.clear()
            self._shared_session_cache.clear()
            for slot in self._slots.values():
                slot["in_use"] = False
                slot["session_id"] = None
                slot["agent_id"] = None
                slot["reuse_count"] = 0
                slot["state_cache"].clear()
                self._clear_dir(Path(slot["scratch_dir"]))
            return {"status": "OK", "flushed_count": flushed}

    @staticmethod
    def _sanitize_slot(slot: dict[str, Any]) -> dict[str, Any]:
        return {
            "slot_id": slot["slot_id"],
            "session_id": slot["session_id"],
            "agent_id": slot["agent_id"],
            "reuse_count": slot["reuse_count"],
            "scratch_dir": slot["scratch_dir"],
            "in_use": slot["in_use"],
            "cached_keys": list(slot["state_cache"].keys()),
        }

    @staticmethod
    def _clear_dir(p: Path) -> None:
        if not p.is_dir():
            return
        for child in p.iterdir():
            with contextlib.suppress(OSError):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)


GLOBAL_SESSION_POOL = SessionPool()


# ═══════════════════════════════════════════════════════════════
# 3. Variable Piping ($prev.*, $steps.<id>.*, $cache.<key>.*, ${var})
# ═══════════════════════════════════════════════════════════════

_REF_EXACT_PATTERN = re.compile(r"^\$(prev|(?:steps|cache|slot)(?:\.([A-Za-z0-9_\-]+))?)(?:\.(.+))?$")
_REF_EMBED_PATTERN = re.compile(r"\$((?:prev|steps|cache|slot)(?:\.[A-Za-z0-9_\-]+)+)")
_STRUCT_WHOLE = re.compile(r"^\$\{(\w+)\}$")
_STRUCT_EMBED = re.compile(r"\$\{(\w+)\}")


def hydrate_struct(obj: Any, variables: dict[str, Any], strict: bool = False) -> Any:
    """Structural substitution of `${var}` in dict/list values without eval."""

    def sub_str(s: str) -> Any:
        whole = _STRUCT_WHOLE.match(s)
        if whole:
            name = whole.group(1)
            if name in variables:
                return variables[name]
            if strict:
                raise ValueError(f"Missing required variable: ${{{name}}}")
            return s

        def repl(mo: re.Match[str]) -> str:
            name = mo.group(1)
            if name in variables:
                return str(variables[name])
            if strict:
                raise ValueError(f"Missing required variable: ${{{name}}}")
            return mo.group(0)

        return _STRUCT_EMBED.sub(repl, s)

    if isinstance(obj, dict):
        return {k: hydrate_struct(v, variables, strict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [hydrate_struct(x, variables, strict) for x in obj]
    if isinstance(obj, str):
        return sub_str(obj)
    return obj


def _get_dot_path(data: Any, path_str: str) -> Any:
    """Navigate dictionary keys and list indices using dot-notation."""
    if not path_str:
        return data
    curr = data
    for part in path_str.split("."):
        if curr is None:
            return None
        if isinstance(curr, dict):
            if part in curr:
                curr = curr[part]
            else:
                return None
        elif isinstance(curr, (list, tuple)):
            try:
                idx = int(part)
                if 0 <= idx < len(curr):
                    curr = curr[idx]
                else:
                    return None
            except ValueError:
                return None
        else:
            return None
    return curr


def _resolve_target_field(target: Any, dot_path: str) -> Any:
    """Resolve dot_path against target with transparent fallback unwrapping for output/items/data."""
    if not dot_path:
        return target
    val = _get_dot_path(target, dot_path)
    if val is not None:
        return val
    if isinstance(target, dict):
        for wrapper_key in ("output", "data", "result", "items"):
            inner = target.get(wrapper_key)
            if isinstance(inner, (dict, list)):
                val = _get_dot_path(inner, dot_path)
                if val is not None:
                    return val
    return None


def resolve_step_refs(
    params: Any,
    step_outputs: dict[str, Any],
    prev_output: Any = None,
    slot_cache: dict[str, Any] | None = None,
) -> Any:
    """Recursively resolve `$prev.*`, `$steps.<id>.*`, `$cache.<key>.*`, `$slot.<key>.*`."""
    if isinstance(params, dict):
        return {
            k: resolve_step_refs(v, step_outputs, prev_output, slot_cache=slot_cache)
            for k, v in params.items()
        }
    if isinstance(params, list):
        return [
            resolve_step_refs(v, step_outputs, prev_output, slot_cache=slot_cache)
            for v in params
        ]
    if not isinstance(params, str):
        return params

    text = params.strip()
    m_exact = _REF_EXACT_PATTERN.match(text)
    if m_exact:
        root_scope = m_exact.group(1)
        sub_step_id = m_exact.group(2)
        dot_path = m_exact.group(3) or ""

        if root_scope == "prev":
            return _resolve_target_field(prev_output, dot_path)
        if root_scope.startswith("steps"):
            return _resolve_target_field(step_outputs.get(sub_step_id or ""), dot_path)
        if root_scope.startswith(("cache", "slot")):
            return _resolve_target_field((slot_cache or {}).get(sub_step_id or ""), dot_path)

    def _repl(m: re.Match[str]) -> str:
        token = m.group(1)
        if token.startswith("prev"):
            sub_path = token[4:].lstrip(".")
            val = _resolve_target_field(prev_output, sub_path)
            return str(val) if val is not None else m.group(0)
        if token.startswith("steps."):
            parts = token[6:].split(".", 1)
            step_id = parts[0]
            sub_path = parts[1] if len(parts) > 1 else ""
            val = _resolve_target_field(step_outputs.get(step_id), sub_path)
            return str(val) if val is not None else m.group(0)
        if token.startswith(("cache.", "slot.")):
            prefix_len = 6 if token.startswith("cache.") else 5
            parts = token[prefix_len:].split(".", 1)
            k = parts[0]
            sub_path = parts[1] if len(parts) > 1 else ""
            val = _resolve_target_field((slot_cache or {}).get(k), sub_path)
            return str(val) if val is not None else m.group(0)
        return m.group(0)

    return _REF_EMBED_PATTERN.sub(_repl, params)


# ═══════════════════════════════════════════════════════════════
# 4. Server-Side Dot-Path Field Projection (`select_fields`)
# ═══════════════════════════════════════════════════════════════


def _set_dot_path(
    dest: Any,
    path_str: str,
    value: Any,
    source: Any = None,
    idx_maps: dict[int, dict[int, int]] | None = None,
) -> None:
    maps = idx_maps if idx_maps is not None else {}
    parts = path_str.split(".")
    curr = dest
    src = source
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(src, list) and isinstance(curr, list) and part.isdigit():
            orig = int(part)
            m = maps.setdefault(id(curr), {})
            if orig not in m:
                curr.append(None)
                m[orig] = len(curr) - 1
            pos = m[orig]
            child_src = src[orig] if 0 <= orig < len(src) else None
            if last:
                curr[pos] = value
                return
            if not isinstance(curr[pos], (dict, list)):
                curr[pos] = [] if isinstance(child_src, list) else {}
            curr, src = curr[pos], child_src
            continue
        child_src = src.get(part) if isinstance(src, dict) else None
        if last:
            curr[part] = value
            return
        if not isinstance(curr.get(part), (dict, list)):
            curr[part] = [] if isinstance(child_src, list) else {}
        curr, src = curr[part], child_src


def project_fields(data: Any, select_fields: list[str] | None) -> Any:
    """Server-side dot-path field projection reducing response payload size in RAM."""
    if not select_fields or not isinstance(data, dict):
        return data

    projected: dict[str, Any] = {}
    idx_maps: dict[int, dict[int, int]] = {}
    unresolved: list[str] = []

    for field_path in select_fields:
        clean_path = str(field_path).strip()
        if not clean_path:
            continue
        val = _get_dot_path(data, clean_path)
        if val is not None:
            _set_dot_path(projected, clean_path, val, source=data, idx_maps=idx_maps)
            continue

        output_obj = data.get("output")
        if isinstance(output_obj, dict):
            out_val = _get_dot_path(output_obj, clean_path)
            if out_val is not None:
                _set_dot_path(projected, clean_path, out_val)
                _set_dot_path(projected, f"output.{clean_path}", out_val)
                continue

        results_list = data.get("results")
        step_matched = False
        if isinstance(results_list, list):
            for idx, r in enumerate(results_list):
                if not isinstance(r, dict):
                    continue
                step_val = None
                if isinstance(r.get("output"), dict):
                    step_val = _get_dot_path(r["output"], clean_path)
                if step_val is None:
                    step_val = _get_dot_path(r, clean_path)
                if step_val is not None:
                    step_matched = True
                    if "results" not in projected:
                        projected["results"] = []
                    while len(projected["results"]) <= idx:
                        mr = results_list[len(projected["results"])]
                        projected["results"].append(
                            {
                                "id": mr.get("id"),
                                "tool": mr.get("tool"),
                                "status": mr.get("status"),
                            }
                        )
                    _set_dot_path(projected["results"][idx], clean_path, step_val)
        if not step_matched:
            unresolved.append(clean_path)

    if unresolved:
        projected["unresolved_fields"] = unresolved
    return projected


# ═══════════════════════════════════════════════════════════════
# 5. Topos DAG Guard & Topological Step Ordering
# ═══════════════════════════════════════════════════════════════


class ToposVerdict:
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    WARN = "WARN"


class ToposGuard:
    """Ontological DAG validator preventing circular co-dependencies (A <-> B) in batch/playbooks."""

    @staticmethod
    def audit_task_dag(
        tasks: list[str],
        dependencies: dict[str, list[str]],
    ) -> tuple[str, list[str]]:
        adj: dict[str, list[str]] = {t: [] for t in tasks}
        for task, deps in dependencies.items():
            if task not in adj:
                adj[task] = []
            for d in deps:
                if d in adj:
                    adj[task].append(d)

        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycle_nodes: list[str] = []

        def dfs(node: str, path: list[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor, path):
                        return True
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle_nodes.extend(path[cycle_start:])
                    cycle_nodes.append(neighbor)
                    return True
            rec_stack.remove(node)
            path.pop()
            return False

        for t in tasks:
            if t not in visited and dfs(t, []):
                cycle_repr = " -> ".join(cycle_nodes)
                return (
                    ToposVerdict.BLOCKED,
                    [
                        f"Circular co-dependency detected: [{cycle_repr}].",
                        "Topos axiom violated: co-dependent steps are blocked.",
                    ],
                )

        return (ToposVerdict.APPROVED, ["Coherent DAG: zero co-dependencies detected."])

    @classmethod
    def validate_and_order(cls, steps: list[dict[str, Any]]) -> dict[str, Any]:
        norm: list[dict[str, Any]] = []
        for i, s in enumerate(steps):
            if not isinstance(s, dict) or ("tool" not in s and "action" not in s):
                raise ValueError(f"Invalid step at index {i}: missing 'tool'")
            item = dict(s)
            item["id"] = str(s.get("id") or f"s{i + 1}")
            item["tool"] = str(s.get("tool") or s.get("action"))
            item["params"] = dict(s.get("params") or {})
            item["depends_on"] = [str(d) for d in (s.get("depends_on") or [])]
            norm.append(item)

        ids = [s["id"] for s in norm]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate step IDs in batch/playbook")

        deps = {s["id"]: [d for d in s["depends_on"] if d in ids] for s in norm}
        verdict, logs = cls.audit_task_dag(ids, deps)
        if verdict != ToposVerdict.APPROVED:
            return {"ok": False, "verdict": verdict, "logs": logs}

        indeg = {i: 0 for i in ids}
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for sid, dlist in deps.items():
            for d in dlist:
                adj[d].append(sid)
                indeg[sid] += 1

        queue = [i for i in ids if indeg[i] == 0]
        order: list[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)

        if len(order) != len(ids):
            return {"ok": False, "verdict": ToposVerdict.BLOCKED, "logs": ["Incomplete topological order (cycle)"]}

        by_id = {s["id"]: s for s in norm}
        return {
            "ok": True,
            "verdict": verdict,
            "order": order,
            "ordered_steps": [by_id[i] for i in order],
            "steps": norm,
        }
