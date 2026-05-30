"""Internal: per-type LLM specialists (contradiction adjudicator + refinement picker).

Adapted from agent-merge-bench's routed_resolver. Same prompts; refactored to
take MemoryWrite (the resolver-oss schema) and return primitive tuples that
TypedMergePolicy turns into MergeDecisions.
"""
from __future__ import annotations

from ._llm import call_with_cache, coerce_float, parse_json_response
from .policy import MemoryWrite


def _build_contradiction_prompt(incoming: MemoryWrite, existing: MemoryWrite) -> str:
    lines = [
        "You adjudicate a CONTRADICTION between two memories about the same subject.",
        "They are mutually exclusive — one is wrong. Pick the correct one, or escalate.",
        "",
        "DECISION RULES (in priority order):",
        "",
        "1. CORRECTIONS WIN. A recent user_statement from a chat-style author about the user's",
        "   own preference, role, state, or correction overrides an older inferred or extracted fact.",
        "",
        "2. STATE MUTATIONS — later wins. If both claims observe an evolving state",
        "   (ticket status, role, system state, deployment status), the more recent",
        "   observation reflects the current state.",
        "",
        "3. EVIDENCE QUALITY (corrected). tool_result and document with a specific",
        "   evidence reference are grounded. **BUT absence of evidence is NOT weak by",
        "   default** — many memory substrates don't persist per-fact citations, so",
        "   absence reflects substrate design rather than writer reliability. Treat",
        "   unsourced writes from competent automated writers as on par with cited",
        "   writes when the substrate is the reason no citation exists.",
        "",
        "4. SPECIFICITY as a tie-breaker. When the above don't resolve, pick the claim",
        "   with more specific concrete detail (exact values, named entities, narrower scope).",
        "",
        "If none of these resolve confidently, escalate.",
        "",
        "Existing memory (incumbent):",
        f"  text: {existing.text}",
        f"  id: {existing.id}",
        f"  author: {existing.author or '<unknown>'}",
        f"  timestamp: {existing.updated_at or existing.created_at or '<unknown>'}",
        f"  evidence: {','.join(existing.evidence) if existing.evidence else '<none>'}",
        f"  confidence: {existing.confidence if existing.confidence is not None else '<unknown>'}",
        "",
        "Incoming memory (challenger):",
        f"  text: {incoming.text}",
        f"  id: {incoming.id}",
        f"  author: {incoming.author or '<unknown>'}",
        f"  timestamp: {incoming.updated_at or incoming.created_at or '<unknown>'}",
        f"  evidence: {','.join(incoming.evidence) if incoming.evidence else '<none>'}",
        f"  confidence: {incoming.confidence if incoming.confidence is not None else '<unknown>'}",
        "",
        "Respond with ONE JSON object and nothing else:",
        "{",
        '  "decision": "winner" | "escalate",',
        '  "winner": "incoming" | "existing",',
        '  "confidence": <number in [0.0, 1.0]>,',
        '  "reasoning": "<one short sentence>"',
        "}",
    ]
    return "\n".join(lines)


def handle_contradiction(
    *,
    incoming: MemoryWrite,
    existing: MemoryWrite,
    model: str,
    api_key: str | None = None,
) -> tuple[bool, float, str, bool]:
    """Returns (winner_is_incoming, confidence, rationale, escalate).

    If escalate=True, the resolver should ESCALATE; winner_is_incoming is undefined.
    """
    prompt = _build_contradiction_prompt(incoming, existing)
    raw = call_with_cache(model, "contra-specialist", prompt, api_key=api_key)
    try:
        payload = parse_json_response(raw)
    except ValueError as e:
        return False, 0.0, f"contradiction specialist parse error: {e}", True

    decision = str(payload.get("decision", "")).strip().lower()
    if decision == "escalate":
        return False, coerce_float(payload.get("confidence")), str(payload.get("reasoning", ""))[:300], True

    winner = str(payload.get("winner", "")).strip().lower()
    winner_is_incoming = winner == "incoming"
    return winner_is_incoming, coerce_float(payload.get("confidence")), str(payload.get("reasoning", ""))[:300], False


def _build_refinement_prompt(incoming: MemoryWrite, existing: MemoryWrite) -> str:
    # Refinement specialist deliberately HIDES evidence/confidence to prevent
    # evidence-quality theater (see agent-merge-bench docs/v0-leaderboard.md
    # ablation finding: evidence hierarchy adds 0pp on this kind of decision).
    lines = [
        "Two memories about the same subject differ in specificity. One is a refinement",
        "(adds detail, version, qualifier, named entities). Your job: pick the more specific one.",
        "",
        "Specificity signals to look for:",
        "- Named entities (versions, exact dates, named tickets, IDs, framework names)",
        "- Numbers vs vague quantifiers ('1,000 users' beats 'around 1,000')",
        "- Conditions/qualifiers (e.g. 'for production at AcmeCo' beats unqualified)",
        "",
        "If both are equally specific, pick the LATER one (by timestamp).",
        "",
        "Existing memory:",
        f"  text: {existing.text}",
        f"  author: {existing.author or '<unknown>'}",
        f"  timestamp: {existing.updated_at or existing.created_at or '<unknown>'}",
        "",
        "Incoming memory:",
        f"  text: {incoming.text}",
        f"  author: {incoming.author or '<unknown>'}",
        f"  timestamp: {incoming.updated_at or incoming.created_at or '<unknown>'}",
        "",
        "Respond with ONE JSON object and nothing else:",
        "{",
        '  "winner": "incoming" | "existing",',
        '  "confidence": <number in [0.0, 1.0]>,',
        '  "reasoning": "<one short sentence>"',
        "}",
    ]
    return "\n".join(lines)


def handle_refinement(
    *,
    incoming: MemoryWrite,
    existing: MemoryWrite,
    model: str,
    api_key: str | None = None,
) -> tuple[bool, float, str]:
    """Returns (winner_is_incoming, confidence, rationale)."""
    prompt = _build_refinement_prompt(incoming, existing)
    raw = call_with_cache(model, "refine-specialist", prompt, api_key=api_key)
    try:
        payload = parse_json_response(raw)
    except ValueError as e:
        # Refinement parse error → default to incoming wins (newer); low confidence
        return True, 0.0, f"refinement parse error: {e}"
    winner = str(payload.get("winner", "")).strip().lower()
    return winner == "incoming", coerce_float(payload.get("confidence")), str(payload.get("reasoning", ""))[:300]
