"""Stateful Transit+JSON decoder for Penpot API responses.

Penpot uses Cognitect's Transit format (transit+json) for all API responses.
This decoder handles caching (^X references) needed for proper decoding.

Transit cache encoding: index N → chr(48 + N) for single-char (N < 44),
two-char for N >= 44.

Cache rule (from transit-java WriteCache.isCacheable):
  Strings >= 4 chars that are EITHER:
    - Map keys (asMapKey=true), OR
    - Keywords (~:), symbols (~$), or tags (~#) regardless of position
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_CACHE_BASE = 44
_CACHE_BASE_CHAR = 48  # ASCII '0'

# Tag chars that are always cacheable (keywords, symbols, compound tags)
_ALWAYS_CACHEABLE_TAGS = frozenset((":", "$", "#"))


def decode_transit(data: str | Any) -> Any:
    """Decode a Transit+JSON string or parsed structure into Python objects."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data
    cache = _Cache()
    return _decode(data, cache, as_key=False)


class _Cache:
    """Transit string cache using chr(48+N) encoding."""

    def __init__(self):
        self._map: dict[str, str] = {}
        self._idx = 0

    def _ref_for(self, idx: int) -> str:
        if idx < _CACHE_BASE:
            return f"^{chr(_CACHE_BASE_CHAR + idx)}"
        hi = idx // _CACHE_BASE
        lo = idx % _CACHE_BASE
        return f"^{chr(_CACHE_BASE_CHAR + hi)}{chr(_CACHE_BASE_CHAR + lo)}"

    def cache(self, s: str, as_key: bool = False) -> None:
        """Cache a string if it meets transit-java's cacheability rules.

        From transit-java WriteCache.isCacheable:
          len >= 4 AND (asMapKey OR (s[0]=='~' AND s[1] in {':', '$', '#'}))
        """
        if len(s) < 4:
            return
        if as_key or (s[0] == "~" and len(s) >= 2 and s[1] in _ALWAYS_CACHEABLE_TAGS):
            ref = self._ref_for(self._idx)
            self._map[ref] = s
            self._idx += 1

    def resolve(self, ref: str) -> str | None:
        """Resolve a ^X cache reference. Returns None if not found."""
        return self._map.get(ref)

    @staticmethod
    def is_cache_ref(s: str) -> bool:
        return len(s) >= 2 and s[0] == "^"


def _decode(val: Any, cache: _Cache, as_key: bool = False) -> Any:
    if isinstance(val, str):
        return _decode_str(val, cache, as_key)
    if isinstance(val, list):
        return _decode_list(val, cache)
    if isinstance(val, dict):
        # Transit JSON-Verbose: single-key dicts with "~#tag" key are tagged values
        if len(val) == 1:
            k = next(iter(val))
            if isinstance(k, str) and k.startswith("~#"):
                cache.cache(k, as_key=False)
                tag = k[2:]
                payload = val[k]
                return _decode_tagged_verbose(tag, payload, cache)
        out = {}
        for k, v in val.items():
            dk = _decode_str(k, cache, as_key=True) if isinstance(k, str) else k
            dv = _decode(v, cache)
            out[dk] = dv
        return out
    return val


def _decode_str(s: str, cache: _Cache, as_key: bool = False) -> Any:
    # Cache reference: ^X — resolve and return
    if _Cache.is_cache_ref(s):
        resolved = cache.resolve(s)
        if resolved is not None:
            return _parse_tagged(resolved)
        return s

    # Cache the string per transit-java rules
    cache.cache(s, as_key)

    # Tagged string: ~X...
    if len(s) >= 2 and s[0] == "~":
        return _parse_tagged(s)

    return s


def _parse_tagged(s: str) -> Any:
    """Decode a ~X tagged string value."""
    if len(s) < 2 or s[0] != "~":
        return s

    tag = s[1]
    rest = s[2:]

    if tag == ":":  # keyword
        return rest
    if tag == "u":  # UUID
        return rest
    if tag == "m":  # instant (ms)
        try:
            return datetime.fromtimestamp(int(rest) / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return s
    if tag == "t":  # date string
        return rest
    if tag == "?":  # boolean
        return rest == "t"
    if tag == "i":  # integer
        try:
            return int(rest)
        except ValueError:
            return s
    if tag == "d":  # double
        try:
            return float(rest)
        except ValueError:
            return s
    if tag == "n":  # bigint
        try:
            return int(rest)
        except ValueError:
            return s
    if tag == "~":  # escaped ~
        return "~" + rest
    if tag == "^":  # escaped ^
        return "^" + rest
    return s


def _decode_list(lst: list, cache: _Cache) -> Any:
    if not lst:
        return []

    first = lst[0]

    # Transit map: ["^ ", key, val, key, val, ...]
    if first == "^ ":
        out = {}
        items = lst[1:]
        i = 0
        while i < len(items) - 1:
            key = _decode(items[i], cache, as_key=True)
            val = _decode(items[i + 1], cache)
            out[key] = val
            i += 2
        return out

    # Resolve the tag string — may be a literal "~#tag" or a cache ref "^X"
    tag_str = None
    if isinstance(first, str):
        if first.startswith("~#"):
            tag_str = first
            # ~# tags are always cacheable per isCacheable rule
            cache.cache(first, as_key=False)
        elif _Cache.is_cache_ref(first):
            resolved = cache.resolve(first)
            if resolved is not None and resolved.startswith("~#"):
                tag_str = resolved

    # Tagged value: ["~#tag", payload]
    if tag_str is not None and len(lst) >= 2:
        tag = tag_str[2:]
        payload = lst[1]
        if tag == "set":
            elems = payload if isinstance(payload, list) else [payload]
            return [_decode(e, cache) for e in elems]
        if tag == "list":
            elems = payload if isinstance(payload, list) else [payload]
            return [_decode(e, cache) for e in elems]
        if tag == "cmap":
            if isinstance(payload, list):
                out = {}
                i = 0
                while i < len(payload) - 1:
                    key = _decode(payload[i], cache, as_key=True)
                    val = _decode(payload[i + 1], cache)
                    out[key] = val
                    i += 2
                return out
        if tag == "ordered-set":
            elems = payload if isinstance(payload, list) else [payload]
            return [_decode(e, cache) for e in elems]
        # Generic tagged value (shape, matrix, point, rect)
        return _decode(payload, cache)

    # Regular array
    return [_decode(v, cache) for v in lst]


def _decode_tagged_verbose(tag: str, payload: Any, cache: _Cache) -> Any:
    """Decode a Transit JSON-Verbose tagged value {"~#tag": payload}."""
    if tag == "uri":
        return payload if isinstance(payload, str) else str(payload)
    if tag == "set":
        elems = payload if isinstance(payload, list) else [payload]
        return [_decode(e, cache) for e in elems]
    if tag == "list":
        elems = payload if isinstance(payload, list) else [payload]
        return [_decode(e, cache) for e in elems]
    if tag == "cmap":
        if isinstance(payload, list):
            out = {}
            i = 0
            while i < len(payload) - 1:
                key = _decode(payload[i], cache, as_key=True)
                val = _decode(payload[i + 1], cache)
                out[key] = val
                i += 2
            return out
    if tag == "ordered-set":
        elems = payload if isinstance(payload, list) else [payload]
        return [_decode(e, cache) for e in elems]
    # Generic tagged value
    return _decode(payload, cache)
