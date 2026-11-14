"""Shared AEP Code Mode engine for sandboxed MCP servers.

Provides token-efficient single-tool execution (`discover` | `call` | `code`
pipeline), `$steps.<id>.<field>` / `$vars.<name>` parameter piping, and
`verbosity` / `return_only` / `select_fields` response compaction.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

_STEP_REF_RE = re.compile(r"^\$steps\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)$")
_VAR_REF_RE = re.compile(r"^\$vars\.([A-Za-z0-9_-]+)$")


def _lookup_dotted(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def resolve_step_refs(
    value: Any,
    step_outputs: dict[str, dict[str, Any]],
    vars_dict: dict[str, Any] | None = None,
) -> Any:
    """Recursively resolve `$steps.<id>.<field>` and `$vars.<name>` references."""
    if isinstance(value, str):
        m_step = _STEP_REF_RE.match(value.strip())
        if m_step:
            sid, field = m_step.groups()
            found = _lookup_dotted(step_outputs.get(sid, {}), field)
            return found if found is not None else value
        m_var = _VAR_REF_RE.match(value.strip())
        if m_var and vars_dict is not None:
            vname = m_var.group(1)
            return vars_dict.get(vname, value)
        return value
    if isinstance(value, list):
        return [resolve_step_refs(item, step_outputs, vars_dict) for item in value]
    if isinstance(value, dict):
        return {k: resolve_step_refs(v, step_outputs, vars_dict) for k, v in value.items()}
    return value


def compact_payload(
    payload: dict[str, Any],
    *,
    verbosity: str = "summary",
    return_only: str = "summary",
    select_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Apply AEP token-saving projection to an MCP tool response."""
    if select_fields:
        projected = {k: payload[k] for k in select_fields if k in payload}
        if "status" not in projected and "status" in payload:
            projected["status"] = payload["status"]
        return projected
    verb = (verbosity or "summary").strip().lower()
    ret = (return_only or "summary").strip().lower()
    if verb == "full" or ret == "all":
        return payload
    if verb == "minimal":
        keep = ("status", "output_path", "summary", "steps_run", "final_output")
        return {k: payload[k] for k in keep if k in payload}
    # Default 'summary': keep key fields and compact nested step outputs
    out = dict(payload)
    if "steps" in out and isinstance(out["steps"], list):
        out["steps"] = [
            {
                k: s[k]
                for k in ("id", "action", "ok", "output_path", "error")
                if k in s
            }
            for s in out["steps"]
            if isinstance(s, dict)
        ]
    return out


async def execute_code_pipeline(
    steps: list[dict[str, Any]],
    dispatcher: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    vars_dict: dict[str, Any] | None = None,
    stop_on_error: bool = True,
    return_only: str = "summary",
    select_fields: list[str] | None = None,
    verbosity: str = "summary",
) -> dict[str, Any]:
    """Execute a multi-step Code Mode pipeline with `$steps.<id>.<field>` piping."""
    step_outputs: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    last_output: dict[str, Any] = {}

    for idx, raw_step in enumerate(steps):
        sid = str(raw_step.get("id") or f"step_{idx + 1}")
        action = str(raw_step.get("action") or raw_step.get("tool") or "")
        raw_params = raw_step.get("params") if isinstance(raw_step.get("params"), dict) else {
            k: v for k, v in raw_step.items() if k not in {"id", "action", "tool", "depends_on"}
        }
        resolved_params = resolve_step_refs(raw_params, step_outputs, vars_dict)
        try:
            res = await dispatcher(action, resolved_params)
            step_outputs[sid] = res
            last_output = res
            results.append(
                {
                    "id": sid,
                    "action": action,
                    "ok": True,
                    "output_path": res.get("output_path") or res.get("path"),
                    "output": res,
                }
            )
        except Exception as exc:
            results.append({"id": sid, "action": action, "ok": False, "error": str(exc)})
            if stop_on_error:
                return compact_payload(
                    {
                        "status": "ERROR",
                        "steps_run": len(results),
                        "failed_step": sid,
                        "error": str(exc),
                        "steps": results,
                    },
                    verbosity=verbosity,
                    return_only=return_only,
                    select_fields=select_fields,
                )

    payload: dict[str, Any] = {
        "status": "OK",
        "steps_run": len(results),
        "output_path": last_output.get("output_path") or last_output.get("path"),
        "final_output": last_output,
        "steps": results,
    }
    return compact_payload(
        payload,
        verbosity=verbosity,
        return_only=return_only,
        select_fields=select_fields,
    )
