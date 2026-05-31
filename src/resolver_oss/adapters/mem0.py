"""Mem0 adapter — simulates Mem0's 4-op fact-management vocabulary.

Mem0's current pipeline (per https://github.com/mem0ai/mem0 + arXiv:2504.19413):
- LLM emits one of `{ADD, UPDATE, DELETE, NOOP}` per incoming fact
- UPDATE collapses both REFINEMENT (more-specific) and COMPLEMENTARY (different facet)
  into the same bucket
- CONTRADICTION → DELETE-old + ADD-new (effectively recency)
- DUPLICATE → NOOP

This adapter:
- Mode A (stock): emulate Mem0's 4-op behavior. For our bench scenarios
  (always conflict pairs on same subject), stock = LLM-picks-UPDATE-or-NOOP,
  approximated as recency.
- Mode B (with policy): route through the 5-action MergePolicy. Map our actions
  to Mem0 semantics for comparison.

SCOPE: simulation of the merge layer. Full end-to-end Mem0 integration would
require Mem0 client setup + memory.add() calls; deferred to v0.2 once a Mem0
maintainer signals interest (the RFC draft addresses this).

USAGE:
    from resolver_oss.adapters.mem0 import Mem0BenchAdapter
    from resolver_oss import TypedMergePolicy
    adapter = Mem0BenchAdapter(policy=TypedMergePolicy())
    # use with agent-merge-bench evaluate(adapter, scenarios)
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..policy import MemoryWrite, MergePolicy

if TYPE_CHECKING:
    from agent_merge_bench.schema import Decision, Scenario


def _resolve_stock_mem0(writes_mw: list[MemoryWrite]) -> dict[str, tuple[str, str]]:
    """Approximation of Mem0's stock 4-op behavior on conflict pairs.

    Mem0's LLM picks ADD/UPDATE/DELETE/NOOP per fact. For two writes about the
    same subject, the dominant pattern is:
    - Different facets → UPDATE (overwrites; loses earlier content)
    - Same fact restated → NOOP (keep first)
    - Different state → UPDATE (newer wins; same as recency for state mutations)

    On the conflict-pair distribution: behavior collapses to "last write wins
    on same subject" because Mem0's UPDATE merges into the stored fact.
    For our purposes: stock Mem0 ≈ recency on the bench scenarios.
    """
    final: dict[str, tuple[str, str]] = {}
    for w in writes_mw:
        # Mem0 uses a stable subject-derived id; for our scenarios both writes
        # are about the same subject → same id → UPDATE on second write.
        sid = "mem0-single-id"
        final[sid] = ("Memory", w.text)
    return final


def _apply_policy_decision_mem0(
    incoming: MemoryWrite,
    existing_dict: dict[str, tuple[str, str]],
    decision,
) -> dict[str, tuple[str, str]]:
    """Map our 5-action vocabulary onto Mem0's storage semantics."""
    final = dict(existing_dict)
    sid = "mem0-single-id"

    if decision.action == "ADD":
        # Mem0 would ADD with a fresh id → both kept
        new_sid = f"{sid}-add"
        final[new_sid] = ("Memory", incoming.text)
    elif decision.action in ("UPDATE", "SUPERSEDE_BY_TIME"):
        # Both map to Mem0 UPDATE (overwrites)
        final[sid] = ("Memory", incoming.text)
    elif decision.action == "DEDUPE":
        # Mem0 NOOP — keep existing as-is
        pass
    elif decision.action == "ESCALATE":
        # Mem0 has no ESCALATE action. Closest semantic: ADD-both with a
        # marker. In practice users would route to a callback.
        esc_sid = f"{sid}-esc"
        final[esc_sid] = ("Memory", f"[ESCALATED] {incoming.text}")
    return final


class Mem0BenchAdapter:
    """Mem0-shaped merge adapter for agent-merge-bench scoring."""

    def __init__(self, policy: MergePolicy | None = None, *, name_suffix: str = "") -> None:
        self.policy = policy
        policy_name = getattr(policy, "name", "stock") if policy else "stock"
        self.name = f"mem0/{policy_name}{name_suffix}"

    def resolve(self, scenario: "Scenario") -> "Decision":
        from agent_merge_bench.schema import Decision, DecisionKind

        writes_mw = []
        for w in scenario.writes:
            ts = None
            ts_str = getattr(w, "timestamp", None)
            if isinstance(ts_str, str):
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            writes_mw.append(MemoryWrite(
                id=None, kind="Memory", content=str(w.claim),
                created_at=ts, updated_at=ts,
                author=w.author,
                evidence=(w.evidence_ref,) if w.evidence_ref else None,
                confidence=w.confidence,
            ))

        if self.policy is None:
            # Stock Mem0 behavior simulation
            final = _resolve_stock_mem0(writes_mw)
        else:
            # With policy: apply first as existing, second as incoming
            final: dict[str, tuple[str, str]] = {}
            for i, w in enumerate(writes_mw):
                if i == 0:
                    final["mem0-single-id"] = ("Memory", w.text)
                    continue
                existing_mws = [
                    MemoryWrite(id=sid, kind=kind, content=content)
                    for sid, (kind, content) in final.items()
                ]
                decision = self.policy.handle(w, existing_mws)
                final = _apply_policy_decision_mem0(w, final, decision)

        return _interpret_mem0_final(final, scenario, Decision, DecisionKind)


def _interpret_mem0_final(final, scenario, Decision, DecisionKind):
    """Map final Mem0-shaped store state → bench Decision.

    Mem0 has no explicit ESCALATE so we use the marker convention.
    """
    escalated_entries = [v for v in final.values() if v[1].startswith("[ESCALATED]")]
    non_esc = [v for v in final.values() if not v[1].startswith("[ESCALATED]")]

    if escalated_entries and len(non_esc) <= 1:
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.8,
            reasoning="mem0-adapter: policy returned ESCALATE",
        )

    if len(non_esc) >= 2:
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.8,
            reasoning=f"mem0-adapter: {len(non_esc)} memories preserved (additive)",
        )

    if len(non_esc) == 1:
        _kind, text = non_esc[0]
        # Match text against writes
        for idx, w in enumerate(scenario.writes):
            if text == w.claim:
                return Decision(
                    kind=DecisionKind.WINNER,
                    winner_idx=idx,
                    confidence=0.8,
                    reasoning=f"mem0-adapter: write {idx} retained",
                )

    return Decision(
        kind=DecisionKind.ESCALATE,
        confidence=0.0,
        reasoning="mem0-adapter: could not interpret final state",
    )
