"""Letta adapter — different merge semantics than LangMem/Mem0.

Letta's shared-memory-blocks docs (https://docs.letta.com/tutorials/shared-memory-blocks/):
- `memory_insert(label, text)` — ADDITIVE; appends to existing block
- `memory_replace(label, old_string, new_string)` — overwrite-specific-substring
- Multi-agent: multiple agents share blocks; concurrent writes are additive
  by default (no merge); explicit `memory_replace` is needed for overwriting

This is fundamentally DIFFERENT from LangMem/Mem0:
- LangMem default = collapse (last-write-wins on stable_id)
- Mem0 default = LLM picks ADD/UPDATE/etc.
- Letta default = APPEND (additive); explicit replace required

So for our bench:
- Mode A (stock Letta): both writes APPEND → 2 entries in the block → ESCALATE-equivalent
  (preserves both; never overwrites unless explicit memory_replace)
- Mode B (with policy): typed-merge decides whether to append, replace, or skip

This adapter shows policy POLYMORPHISM: the same TypedMergePolicy works across
hosts with very different default semantics.

SCOPE: simulation, like Mem0Adapter. Full integration with Letta server is v0.2.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..policy import MemoryWrite, MergePolicy

if TYPE_CHECKING:
    from agent_merge_bench.schema import Decision, Scenario


def _resolve_stock_letta(writes_mw: list[MemoryWrite]) -> list[str]:
    """Stock Letta = additive append. Both writes preserved in the block."""
    return [w.text for w in writes_mw]


def _apply_policy_decision_letta(
    block: list[str],
    incoming: MemoryWrite,
    decision,
) -> tuple[list[str], bool]:
    """Map 5-action vocabulary onto Letta block ops.

    Returns (updated block, was_escalated)
    """
    if decision.action == "ADD":
        # Letta memory_insert (additive append)
        return block + [incoming.text], False
    elif decision.action in ("UPDATE", "SUPERSEDE_BY_TIME"):
        # Letta memory_replace — overwrite the most-recent entry
        if not block:
            return [incoming.text], False
        return block[:-1] + [incoming.text], False
    elif decision.action == "DEDUPE":
        # Skip incoming; keep block as-is
        return list(block), False
    elif decision.action == "ESCALATE":
        # Letta has no ESCALATE; preserve both with marker
        return block + [f"[ESCALATED] {incoming.text}"], True
    return block + [incoming.text], False


class LettaBenchAdapter:
    """Letta-shaped merge adapter for agent-merge-bench scoring."""

    def __init__(self, policy: MergePolicy | None = None, *, name_suffix: str = "") -> None:
        self.policy = policy
        policy_name = getattr(policy, "name", "stock") if policy else "stock"
        self.name = f"letta/{policy_name}{name_suffix}"

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
            block = _resolve_stock_letta(writes_mw)
            was_escalated = False
        else:
            block: list[str] = []
            was_escalated = False
            for i, w in enumerate(writes_mw):
                if i == 0:
                    block.append(w.text)
                    continue
                existing_mws = [
                    MemoryWrite(id=f"letta-{j}", kind="Memory", content=line)
                    for j, line in enumerate(block)
                ]
                decision = self.policy.handle(w, existing_mws)
                block, esc = _apply_policy_decision_letta(block, w, decision)
                was_escalated = was_escalated or esc

        return _interpret_letta_final(block, was_escalated, scenario, Decision, DecisionKind)


def _interpret_letta_final(block, was_escalated, scenario, Decision, DecisionKind):
    """Map final Letta block contents → bench Decision."""
    non_esc = [line for line in block if not line.startswith("[ESCALATED]")]
    esc_count = len(block) - len(non_esc)

    if was_escalated or (esc_count > 0 and len(non_esc) <= 1):
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.8,
            reasoning="letta-adapter: policy returned ESCALATE",
        )

    if len(non_esc) >= 2:
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.8,
            reasoning=f"letta-adapter: {len(non_esc)} entries in block (additive)",
        )

    if len(non_esc) == 1:
        text = non_esc[0]
        for idx, w in enumerate(scenario.writes):
            if text == w.claim:
                return Decision(
                    kind=DecisionKind.WINNER,
                    winner_idx=idx,
                    confidence=0.8,
                    reasoning=f"letta-adapter: write {idx} retained",
                )

    return Decision(
        kind=DecisionKind.ESCALATE,
        confidence=0.0,
        reasoning="letta-adapter: empty block",
    )
