"""Internal: LLM-based conflict classifier.

Adapted from agent-merge-bench's routed_resolver. Same prompt structure,
but takes MemoryWrite (resolver-oss schema) instead of Scenario.
"""
from __future__ import annotations

from typing import Literal

from ._llm import call_with_cache, coerce_float, parse_json_response
from .policy import MemoryWrite

ConflictType = Literal["CONTRADICTION", "REFINEMENT", "COMPLEMENTARY", "TEMPORAL_SCOPE", "DUPLICATE"]


def _build_classifier_prompt(incoming: MemoryWrite, existing: MemoryWrite) -> str:
    lines = [
        "You classify the relationship between two memory writes about the same subject.",
        "",
        "Types (pick exactly one):",
        "- contradiction: the claims are mutually exclusive about the SAME attribute in the SAME scope; one must be wrong.",
        "    Includes STATE MUTATIONS: when an attribute that changes over time (user role,",
        "    ticket status, system state, address) is observed at two different times,",
        "    classify as CONTRADICTION (only the current state is operationally true).",
        "- refinement: one claim is strictly a more-specific version of the other (adds detail, narrower qualifier, more named entities). NOT contradictory.",
        "- complementary: the claims describe DIFFERENT attributes/facets of the same entity. Both are true; collapsing them to one would erase information.",
        "- temporal_scope: same attribute, but each claim is INTENTIONALLY scoped to a",
        "    specific time window/region/tenant that is meant to be PRESERVED",
        "    (historical records, per-quarter decisions, multi-tenant pricing).",
        "    Reserve this for cases where both windows are useful to keep — NOT for",
        "    'old state vs current state' (that's CONTRADICTION).",
        "- duplicate: the same fact, only differing in wording or format.",
        "",
        "Your job is ONLY to label the relationship. Do not pick a winner.",
        "",
        f"Existing memory: {existing.text}",
        f"  id: {existing.id}",
        f"  author: {existing.author or '<unknown>'}",
        f"  timestamp: {existing.updated_at or existing.created_at or '<unknown>'}",
        f"  evidence: {','.join(existing.evidence) if existing.evidence else '<none>'}",
        f"  confidence: {existing.confidence if existing.confidence is not None else '<unknown>'}",
        "",
        f"Incoming memory: {incoming.text}",
        f"  id: {incoming.id}",
        f"  author: {incoming.author or '<unknown>'}",
        f"  timestamp: {incoming.updated_at or incoming.created_at or '<unknown>'}",
        f"  evidence: {','.join(incoming.evidence) if incoming.evidence else '<none>'}",
        f"  confidence: {incoming.confidence if incoming.confidence is not None else '<unknown>'}",
        "",
        "Respond with ONE JSON object and nothing else:",
        "{",
        '  "conflict_type": "contradiction" | "refinement" | "complementary" | "temporal_scope" | "duplicate",',
        '  "rationale": "<one short sentence>",',
        '  "confidence": <number in [0.0, 1.0]>',
        "}",
    ]
    return "\n".join(lines)


_TYPE_MAP: dict[str, ConflictType] = {
    "contradiction": "CONTRADICTION",
    "refinement": "REFINEMENT",
    "complementary": "COMPLEMENTARY",
    "temporal_scope": "TEMPORAL_SCOPE",
    "duplicate": "DUPLICATE",
}


def classify_conflict(
    *,
    incoming: MemoryWrite,
    existing: MemoryWrite,
    model: str,
    api_key: str | None = None,
) -> tuple[ConflictType, float, str]:
    """Returns (type, confidence, rationale).

    On classifier failure, defaults to CONTRADICTION with confidence 0.0 and
    a parse-error rationale (so the caller can decide whether to escalate).
    """
    prompt = _build_classifier_prompt(incoming, existing)
    raw = call_with_cache(model, "classifier", prompt, api_key=api_key)
    try:
        payload = parse_json_response(raw)
    except ValueError as e:
        return "CONTRADICTION", 0.0, f"classifier parse error: {e}"
    raw_type = str(payload.get("conflict_type", "")).strip().lower()
    ctype = _TYPE_MAP.get(raw_type)
    if ctype is None:
        return "CONTRADICTION", 0.0, f"unknown conflict_type {raw_type!r}; defaulted"
    return ctype, coerce_float(payload.get("confidence")), str(payload.get("rationale", ""))[:300]
