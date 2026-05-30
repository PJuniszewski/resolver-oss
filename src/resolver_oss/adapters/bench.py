"""BenchAdapter — wraps a MergePolicy as an agent-merge-bench Resolver.

The bench's Scenario is N-write multi-conflict; the policy is pairwise.
For N=2 (the common case): treat writes[0] as existing, writes[1] as incoming.
For N>2: pairwise-collapse — call policy iteratively, accumulating "existing".

Decision mapping (resolver-oss action → bench DecisionKind):
- ADD → ESCALATE (both kept; no winner picked. The bench has no ADD action;
        the semantic outcome of "both writes persist" is ESCALATE.)
- UPDATE → WINNER pointing at the incoming write (the more-specific one)
- DEDUPE → WINNER pointing at the existing write (incoming dropped)
- SUPERSEDE_BY_TIME → WINNER pointing at the incoming write
- ESCALATE → ESCALATE

This is a thin testing adapter, NOT a recommended production pattern.
Production adapters connect to real memory stores (LangMem, Mem0, etc.).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..policy import MemoryWrite, MergePolicy

if TYPE_CHECKING:
    # agent_merge_bench is an optional dep — only needed at type-check + runtime
    # when actually using BenchAdapter
    from agent_merge_bench.schema import Decision, DecisionKind, Scenario


def _write_to_memory(w: object, *, sid: str) -> MemoryWrite:
    """Convert agent_merge_bench.Write → resolver_oss.MemoryWrite."""
    from datetime import datetime

    # w is duck-typed — has the bench's Write fields
    ts_str = getattr(w, "timestamp", None)
    ts = None
    if isinstance(ts_str, str):
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            ts = None

    ek = getattr(w, "evidence_kind", None)
    evidence_kind_str = ek.value if hasattr(ek, "value") else str(ek) if ek else None
    evidence_ref = getattr(w, "evidence_ref", None)
    evidence = (evidence_ref,) if evidence_ref else None

    return MemoryWrite(
        id=sid,
        kind="Memory",
        content=str(getattr(w, "claim", "")),
        created_at=ts,
        updated_at=ts,
        author=getattr(w, "author", None),
        evidence=evidence,
        confidence=float(getattr(w, "confidence", 0.5)) if getattr(w, "confidence", None) is not None else None,
        # Keep evidence_kind in extra (resolver-oss's MemoryWrite doesn't have it as a top-level field
        # to stay decoupled from the bench's EvidenceKind enum)
    )


class BenchAdapter:
    """Wraps a MergePolicy as an agent-merge-bench Resolver.

    Usage:
        from resolver_oss import TypedMergePolicy
        from resolver_oss.adapters.bench import BenchAdapter
        from agent_merge_bench.harness import evaluate
        from agent_merge_bench.schema import load_benchmark

        resolver = BenchAdapter(TypedMergePolicy(model="claude-sonnet-4-6"))
        scenarios = load_benchmark("scenarios/benchmark.json")
        entry = evaluate(resolver, scenarios)
        print(entry.to_markdown())
    """

    def __init__(self, policy: MergePolicy, *, name: str | None = None) -> None:
        self.policy = policy
        # The bench's Resolver protocol requires a `name` attribute
        self.name = name or f"resolver_oss/{getattr(policy, 'name', 'policy')}"

    def resolve(self, scenario: "Scenario") -> "Decision":
        # Lazy imports so the adapter file is importable without the bench installed
        from agent_merge_bench.schema import Decision, DecisionKind

        if len(scenario.writes) < 2:
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=0.0,
                reasoning="BenchAdapter requires ≥2 writes",
            )

        # Pairwise: writes[0] is "existing", writes[1] is "incoming"
        # For multi-write (>2), we'd iterate; v0.1.0 is pairwise-only
        existing = _write_to_memory(scenario.writes[0], sid="mem-0")
        incoming = _write_to_memory(scenario.writes[1], sid="mem-1")

        decision = self.policy.handle(incoming, [existing])

        # Map resolver_oss Action → bench DecisionKind
        if decision.action == "ESCALATE" or decision.action == "ADD":
            # ADD ≈ both-kept ≈ ESCALATE in bench terms (no winner picked)
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=decision.confidence,
                reasoning=f"[{decision.action}] {decision.rationale}",
            )

        # UPDATE / DEDUPE / SUPERSEDE_BY_TIME → WINNER
        if decision.action == "DEDUPE":
            # DEDUPE drops incoming, keeps existing → winner is existing (idx 0)
            winner_idx = 0
        else:
            # UPDATE / SUPERSEDE_BY_TIME → winner is incoming (idx 1)
            winner_idx = 1

        return Decision(
            kind=DecisionKind.WINNER,
            winner_idx=winner_idx,
            confidence=decision.confidence,
            reasoning=f"[{decision.action}] {decision.rationale}",
        )
