"""Internal: anthropic SDK wrapper + disk cache + robust JSON parsing.

Mirrors agent-merge-bench's llm_judge.py infrastructure but kept private to
this library so users don't depend on internals. Re-exported only where
needed (e.g. for the LangMemAdapter to reuse the cache).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Cache version. Bump to force re-fetch (invalidates by key prefix; raw is
# always re-parsed at load time under current parser logic).
CACHE_VERSION = "v1"
CACHE_DIR = Path(os.environ.get("RESOLVER_OSS_CACHE_DIR", ".cache/resolver_oss"))
DEFAULT_MODEL = os.environ.get("RESOLVER_OSS_MODEL", "claude-sonnet-4-6")


def _cache_key(model: str, tag: str, prompt: str) -> str:
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return f"{CACHE_VERSION}__{model}__{tag}__{h}"


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _cached(key: str) -> dict[str, Any] | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _save_cache(key: str, payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps(payload, indent=2) + "\n")


def call_anthropic(
    model: str,
    prompt: str,
    *,
    max_tokens: int = 400,
    api_key: str | None = None,
) -> str:
    """Real API call. No stubs."""
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. `pip install 'resolver-oss[llm]'`"
        ) from e

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. resolver-oss makes real API calls; "
            "set the key or use PassthroughPolicy / RecencyPolicy for offline use."
        )

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: list[str] = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def call_with_cache(
    model: str,
    tag: str,
    prompt: str,
    *,
    api_key: str | None = None,
) -> str:
    """Cached LLM call. Re-runs are free after the first hit."""
    key = _cache_key(model, tag, prompt)
    cached = _cached(key)
    if cached is not None:
        return cached["raw"]
    raw = call_anthropic(model, prompt, api_key=api_key)
    _save_cache(key, {"raw": raw, "prompt": prompt})
    return raw


def parse_json_response(raw: str) -> dict[str, Any]:
    """Robust JSON extraction. Uses json.JSONDecoder.raw_decode which
    handles braces inside string values correctly.
    """
    s = raw.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    start = s.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in response: {raw[:120]!r}")
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(s[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in response: {raw[:120]!r}: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"JSON is not an object: {raw[:120]!r}")
    return obj


def coerce_float(value: Any, default: float = 0.0) -> float:
    """Tolerate non-numeric confidence values from LLM."""
    try:
        f = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(0.0, min(1.0, f))
