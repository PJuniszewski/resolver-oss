"""Core API surface for resolver-oss.

`MergePolicy` is the Protocol that adapters plug into. The library ships three
reference implementations: `TypedMergePolicy` (the headline one), `PassthroughPolicy`
(BC-safe default), `RecencyPolicy` (baseline for comparison).

The 5-action vocabulary is the public contract; the 5-type conflict taxonomy
that motivates it lives at https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/typed-conflict-taxonomy.md
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

# ---------- public schema ----------

Action = Literal["ADD", "UPDATE", "DEDUPE", "SUPERSEDE_BY_TIME", "ESCALATE"]
"""Vocabulary of merge actions.

- ADD: orthogonal new fact (complementary; preserve both)
- UPDATE: incoming strictly more specific (refinement)
- DEDUPE: same fact different phrasing (drop incoming)
- SUPERSEDE_BY_TIME: state mutation, later observation correct (contradiction subtype)
- ESCALATE: cannot confidently resolve; surface to caller
"""


class PolicyError(Exception):
    """Base for resolver-oss-raised errors. Adapters should catch and fall back."""


@dataclass(frozen=True)
class MemoryWrite:
    """One memory candidate — either incoming or existing.

    `author/evidence/confidence` are Optional because most production memory
    stores (Mem0, Zep, LangMem, Letta, Cognee as of 2026-05) don't persist
    per-fact metadata at this granularity. Adapters fill in what's available;
    policies handle missing fields gracefully.
    """

    id: str | None
    """Stable identifier in the store. None for genuinely new (not-yet-stored) writes."""

    kind: str
    """Schema name (e.g. 'Memory', 'UserProfile'). Adapter-specific."""

    content: dict[str, Any] | str
    """Either a dict (structured fact) or a plain string (text memory)."""

    created_at: datetime | None = None
    updated_at: datetime | None = None
    author: str | None = None
    evidence: tuple[str, ...] | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"MemoryWrite.confidence out of range [0,1]: {self.confidence}")

    @property
    def text(self) -> str:
        """Best-effort string representation for prompts."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, dict):
            # try a 'content' field first (LangMem default), then 'text', else stringify
            for key in ("content", "text", "claim", "fact"):
                v = self.content.get(key)
                if isinstance(v, str):
                    return v
            return str(self.content)
        return str(self.content)


@dataclass(frozen=True)
class MergeDecision:
    """Output of policy.handle(...).

    For ADD: target_id is None (creates new).
    For UPDATE / DEDUPE / SUPERSEDE_BY_TIME: target_id MUST match one of the
        existing writes' id values (which existing memory to replace/drop).
    For ESCALATE: target_id is None (no commit happens; adapter routes to caller).
    """

    action: Action
    target_id: str | None = None
    rationale: str = ""
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"MergeDecision.confidence out of range [0,1]: {self.confidence}")
        if self.action in ("UPDATE", "DEDUPE", "SUPERSEDE_BY_TIME") and self.target_id is None:
            raise ValueError(f"{self.action} requires target_id")
        if self.action == "ADD" and self.target_id is not None:
            raise ValueError("ADD must not carry target_id")


# ---------- the Protocol ----------

@runtime_checkable
class MergePolicy(Protocol):
    """Any object with a `handle(incoming, existing) -> MergeDecision`.

    Adapters call this in their commit-time hook. Policy implementations decide
    which action best preserves information given the conflict shape.
    """

    def handle(
        self,
        incoming: MemoryWrite,
        existing: Sequence[MemoryWrite],
    ) -> MergeDecision: ...


# ---------- reference implementations ----------

class PassthroughPolicy:
    """Preserves the upstream system's default behavior.

    Returns UPDATE if incoming.id matches any existing; ADD otherwise.
    Use as adapter default so plugging the policy hook in is BC-safe.
    """

    name = "passthrough"

    def handle(
        self,
        incoming: MemoryWrite,
        existing: Sequence[MemoryWrite],
    ) -> MergeDecision:
        if incoming.id is not None:
            for ex in existing:
                if ex.id == incoming.id:
                    return MergeDecision(
                        action="UPDATE",
                        target_id=ex.id,
                        rationale="passthrough: id-collision update",
                        confidence=1.0,
                    )
        return MergeDecision(
            action="ADD",
            rationale="passthrough: no id collision",
            confidence=1.0,
        )


class RecencyPolicy:
    """Newest write wins. Baseline for comparison.

    Maps to SUPERSEDE_BY_TIME if there's any existing write; ADD otherwise.
    Confidence is always 1.0 (no uncertainty). This is the floor every
    system ships silently.
    """

    name = "recency"

    def handle(
        self,
        incoming: MemoryWrite,
        existing: Sequence[MemoryWrite],
    ) -> MergeDecision:
        if not existing:
            return MergeDecision(action="ADD", rationale="recency: no existing", confidence=1.0)
        # Find the most recent existing and supersede it
        def _ts(w: MemoryWrite) -> datetime:
            return w.updated_at or w.created_at or datetime.min

        latest = max(existing, key=_ts)
        return MergeDecision(
            action="SUPERSEDE_BY_TIME",
            target_id=latest.id,
            rationale="recency: newest write wins",
            confidence=1.0,
        )


# TypedMergePolicy is the headline implementation. Importing it pulls in the LLM client.
# Keep the heavy import lazy so basic users importing the Protocol don't pay for it.

class TypedMergePolicy:
    """LLM-classifier + per-type specialist + deterministic escalation.

    Architecture (one or two LLM calls per resolve):
    1. Classify the conflict type (LLM call): CONTRADICTION / REFINEMENT /
       COMPLEMENTARY / TEMPORAL_SCOPE / DUPLICATE.
    2. Dispatch:
       - CONTRADICTION → refinement specialist with state-mutation rules
         (LLM call) → SUPERSEDE_BY_TIME or ESCALATE
       - REFINEMENT → refinement specialist (LLM call) → UPDATE
       - COMPLEMENTARY → deterministic ESCALATE (no LLM)
       - TEMPORAL_SCOPE → deterministic ESCALATE (no LLM)
       - DUPLICATE → deterministic DEDUPE (no LLM)

    Caveat baked in: this is THE reference baseline from agent-merge-bench,
    which scored 0.967 on synthetic dev. Held-out validation in the
    predecessor project showed 1.000 → 0.650 collapse on real-data — so
    treat single-bench numbers as upper bounds.

    Args:
        model: Anthropic model name. Default 'claude-sonnet-4-6'.
        api_key: ANTHROPIC_API_KEY; falls back to env var.
        skip_when_no_existing: If True, returns ADD immediately when
            existing is empty (saves one LLM call). Default True.
    """

    name = "typed-merge"

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        skip_when_no_existing: bool = True,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.skip_when_no_existing = skip_when_no_existing
        self.name = f"typed-merge ({model})"

    def handle(
        self,
        incoming: MemoryWrite,
        existing: Sequence[MemoryWrite],
    ) -> MergeDecision:
        if not existing and self.skip_when_no_existing:
            return MergeDecision(
                action="ADD",
                rationale="no existing memories; no conflict possible",
                confidence=1.0,
            )

        # Lazy import to keep the Protocol importable without anthropic SDK
        from ._classifier import classify_conflict
        from ._specialists import handle_contradiction, handle_refinement

        if not existing:
            return MergeDecision(action="ADD", confidence=1.0, rationale="no existing")

        # Pairwise comparison against each existing; pick the most-conflicting one.
        # For v0.1.0 we keep it simple: compare against the FIRST existing only.
        # Multi-write (>2) is a v0.2 priority.
        target = existing[0]

        ctype, cconf, crat = classify_conflict(
            incoming=incoming,
            existing=target,
            model=self.model,
            api_key=self._api_key,
        )

        if ctype == "DUPLICATE":
            return MergeDecision(
                action="DEDUPE",
                target_id=target.id,
                rationale=f"deterministic: duplicate ({crat})",
                confidence=cconf,
            )
        if ctype == "COMPLEMENTARY":
            return MergeDecision(
                action="ESCALATE",
                rationale=f"deterministic: complementary; both should persist ({crat})",
                confidence=cconf,
            )
        if ctype == "TEMPORAL_SCOPE":
            return MergeDecision(
                action="ESCALATE",
                rationale=f"deterministic: temporal scope; preserve both ({crat})",
                confidence=cconf,
            )
        if ctype == "REFINEMENT":
            winner_is_incoming, rconf, rrat = handle_refinement(
                incoming=incoming,
                existing=target,
                model=self.model,
                api_key=self._api_key,
            )
            if winner_is_incoming:
                return MergeDecision(
                    action="UPDATE",
                    target_id=target.id,
                    rationale=f"refinement specialist: incoming more specific ({rrat})",
                    confidence=rconf,
                )
            return MergeDecision(
                action="DEDUPE",
                target_id=target.id,
                rationale=f"refinement specialist: existing more specific ({rrat})",
                confidence=rconf,
            )
        if ctype == "CONTRADICTION":
            winner_is_incoming, wconf, wrat, escalate = handle_contradiction(
                incoming=incoming,
                existing=target,
                model=self.model,
                api_key=self._api_key,
            )
            if escalate:
                return MergeDecision(
                    action="ESCALATE",
                    rationale=f"contradiction specialist: cannot adjudicate ({wrat})",
                    confidence=wconf,
                )
            if winner_is_incoming:
                return MergeDecision(
                    action="SUPERSEDE_BY_TIME",
                    target_id=target.id,
                    rationale=f"contradiction specialist: incoming wins ({wrat})",
                    confidence=wconf,
                )
            return MergeDecision(
                action="DEDUPE",
                target_id=target.id,
                rationale=f"contradiction specialist: existing stands ({wrat})",
                confidence=wconf,
            )

        # Classifier returned something unexpected — escalate defensively
        return MergeDecision(
            action="ESCALATE",
            rationale=f"classifier returned unknown type {ctype!r}; escalated",
            confidence=0.0,
        )
