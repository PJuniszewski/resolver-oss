"""LangMem adapter — drop a MergePolicy into LangMem's MemoryStoreManager.

SCOPE OF v0.1.0-alpha:
This adapter targets the MERGE layer of LangMem (`_apply_manager_output` at
`extraction.py:940-975`). It bypasses LangMem's EXTRACTION pipeline (trustcall
LLM call to derive facts from messages) because:
  (a) Extraction is its own LLM call with non-deterministic output — including
      it muddles the comparison between "with policy" vs "without policy"
  (b) The MergePolicy hook we propose to add to LangMem (see langmem-rfc-draft.md)
      lives at the _apply_manager_output layer specifically

For a full end-to-end LangMem test (extraction + merge + retrieval), see v0.2.

USAGE — Mode A (stock LangMem behavior, no policy):

    from resolver_oss.adapters.langmem import LangMemAdapter
    adapter = LangMemAdapter()
    final_state = adapter.simulate_writes([write1, write2])
    # final_state is a dict {stable_id: (kind, content)} reflecting what
    # LangMem's _apply_manager_output would produce if both writes shared
    # the same stable_id (LangMem's last-write-wins on collision).

USAGE — Mode B (with typed-merge policy injected):

    from resolver_oss import TypedMergePolicy
    adapter = LangMemAdapter(policy=TypedMergePolicy())
    final_state = adapter.simulate_writes([write1, write2])
    # Same shape but the merge decision routes through the policy:
    # - ADD: both writes persist (different ids)
    # - UPDATE/SUPERSEDE_BY_TIME: incoming replaces existing
    # - DEDUPE: incoming dropped
    # - ESCALATE: incoming written with `_escalated=True` marker

USAGE — bench integration:

    from resolver_oss.adapters.langmem import LangMemBenchAdapter
    from agent_merge_bench.harness import evaluate
    from agent_merge_bench.schema import load_benchmark

    # Mode A: LangMem-stock
    adapter_stock = LangMemBenchAdapter(policy=None)

    # Mode B: LangMem + TypedMergePolicy
    from resolver_oss import TypedMergePolicy
    adapter_typed = LangMemBenchAdapter(policy=TypedMergePolicy())

    scenarios = load_benchmark("path/to/benchmark.json")
    entry_stock = evaluate(adapter_stock, scenarios)
    entry_typed = evaluate(adapter_typed, scenarios)
    print(f"LangMem-stock:  {entry_stock.accuracy:.3f}")
    print(f"LangMem+typed: {entry_typed.accuracy:.3f}")

HONEST CAVEATS:
- Same-stable-id assumption: in real LangMem, the LLM extractor decides
  whether two memories should share a stable_id (via trustcall's UPDATE
  semantics). Our bench scenarios are constructed as conflicts about the
  same subject, so for the bench we always give them the same stable_id
  (= "LangMem extracted them as the same fact"). For non-bench data, a
  user adapting this would call the real extractor first.
- This is a simulation, not end-to-end. v0.2 priority is to wrap
  MemoryStoreManager.ainvoke() and observe real store state.
- No retrieval scoping. The bench's existing-set is just the prior write;
  in real deployments LangMem's pre-merge retrieval may surface more.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..policy import MemoryWrite, MergePolicy, PassthroughPolicy

if TYPE_CHECKING:
    from agent_merge_bench.schema import Decision, Scenario


# A minimal stand-in for langmem.knowledge.extraction.ExtractedMemory.
# We don't depend on langmem at import time so users can install
# `resolver-oss[langmem]` lazily.

class _ExtractedMemory:
    """Pydantic-lite stand-in for langmem ExtractedMemory namedtuple."""

    def __init__(self, id: str, content: dict[str, Any] | str) -> None:
        self.id = id
        self.content = content


def _stable_id_for_scenario(scenario_id: str, write_idx: int = 0) -> str:
    """Deterministic stable_id per scenario.

    For LangMem-stock mode, both writes get the SAME id (simulating that
    LangMem's extractor identified them as the same fact via UPDATE semantics).
    For LangMem+policy mode, the policy decides what to do.
    """
    # All bench scenarios are conflicts about the same subject, so always
    # same stable_id (simulating "LangMem extracted as same fact").
    return f"mem-{scenario_id}"


def _langmem_apply_manager_output_stock(
    extracted: _ExtractedMemory,
    existing: dict[str, tuple[str, dict | str]],
) -> dict[str, tuple[str, dict | str]]:
    """Reproduce LangMem's _apply_manager_output behavior (extraction.py:940-975).

    Last-write-wins on stable_id collision. New stable_id → ADD.
    """
    new = dict(existing)  # copy
    sid = extracted.id
    content = extracted.content
    kind = "Memory"
    # Unconditional: if sid in store_dict, UPDATE; else INSERT.
    # This matches the current behavior exactly.
    new[sid] = (kind, content)
    return new


class LangMemAdapter:
    """LangMem-shaped merge adapter (simulation; bypasses extraction).

    See module docstring for scope and caveats.
    """

    def __init__(self, policy: MergePolicy | None = None) -> None:
        self.policy = policy or PassthroughPolicy()

    def simulate_writes(
        self,
        writes: list[MemoryWrite],
        *,
        same_stable_id: bool = True,
    ) -> dict[str, tuple[str, dict | str]]:
        """Apply a sequence of writes via the merge layer.

        Returns final store state as {stable_id: (kind, content)}.

        For bench use: same_stable_id=True (writes treated as candidates for
        the same fact, per LangMem's UPDATE semantics).
        """
        store: dict[str, tuple[str, dict | str]] = {}

        for i, write in enumerate(writes):
            # Decide the stable_id for this incoming write
            if same_stable_id and i > 0:
                # Use the same stable_id as the existing → triggers merge
                sid = next(iter(store.keys())) if store else f"mem-{i}"
            else:
                sid = write.id or f"mem-{i}"

            # Build the "existing" view for the policy
            existing_writes: list[MemoryWrite] = []
            for ex_sid, (ex_kind, ex_content) in store.items():
                existing_writes.append(
                    MemoryWrite(
                        id=ex_sid,
                        kind=ex_kind,
                        content=ex_content if isinstance(ex_content, (dict, str)) else str(ex_content),
                    )
                )

            # Build the incoming MemoryWrite (with stable_id set)
            incoming = MemoryWrite(
                id=sid,
                kind=write.kind,
                content=write.content,
                created_at=write.created_at,
                updated_at=write.updated_at,
                author=write.author,
                evidence=write.evidence,
                confidence=write.confidence,
            )

            # Consult the policy
            decision = self.policy.handle(incoming, existing_writes)

            # Apply the decision
            if decision.action == "ADD":
                # Different fact; insert with a fresh id if it would collide
                new_sid = sid if sid not in store else f"{sid}-add-{i}"
                store[new_sid] = (write.kind, write.content)
            elif decision.action == "UPDATE":
                target = decision.target_id or sid
                store[target] = (write.kind, write.content)
            elif decision.action == "DEDUPE":
                # Drop incoming; keep existing as-is
                pass
            elif decision.action == "SUPERSEDE_BY_TIME":
                target = decision.target_id or sid
                store[target] = (write.kind, write.content)
            elif decision.action == "ESCALATE":
                # Write incoming with an _escalated marker. In a real adapter,
                # we'd route to a callback / queue. For the simulation we
                # just keep BOTH writes (one with the marker).
                esc_sid = f"{sid}-esc-{i}"
                marked_content = {"_escalated": True, "_rationale": decision.rationale}
                if isinstance(write.content, str):
                    marked_content["content"] = write.content
                else:
                    marked_content.update(write.content)
                store[esc_sid] = (write.kind, marked_content)

        return store


class LangMemBenchAdapter:
    """Bench-Resolver wrapper around LangMemAdapter for agent-merge-bench scoring.

    Bridges the simulation back to the bench's Decision schema so
    `agent_merge_bench.harness.evaluate(adapter, scenarios)` works.
    """

    def __init__(self, policy: MergePolicy | None = None, *, name_suffix: str = "") -> None:
        self.adapter = LangMemAdapter(policy=policy)
        policy_name = getattr(policy, "name", "stock") if policy else "stock"
        self.name = f"langmem/{policy_name}{name_suffix}"

    def resolve(self, scenario: "Scenario") -> "Decision":
        from agent_merge_bench.schema import Decision, DecisionKind

        # Convert bench Write → MemoryWrite, run the simulation, interpret final state
        writes_mw: list[MemoryWrite] = []
        for w in scenario.writes:
            ts = None
            ts_str = getattr(w, "timestamp", None)
            if isinstance(ts_str, str):
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
            writes_mw.append(MemoryWrite(
                id=None,
                kind="Memory",
                content=str(w.claim),
                created_at=ts,
                updated_at=ts,
                author=w.author,
                evidence=(w.evidence_ref,) if w.evidence_ref else None,
                confidence=w.confidence,
            ))

        final_store = self.adapter.simulate_writes(writes_mw, same_stable_id=True)

        # Interpret final state as a bench Decision
        # - If >1 entries in store: at least one was kept-as-distinct → ESCALATE
        # - If 1 entry: that entry corresponds to a winner; pick the winner_idx
        # - Empty: should never happen (we always write at least one)

        non_escalated = {sid: v for sid, v in final_store.items()
                          if not (isinstance(v[1], dict) and v[1].get("_escalated"))}
        escalated = {sid: v for sid, v in final_store.items()
                     if isinstance(v[1], dict) and v[1].get("_escalated")}

        if escalated and len(non_escalated) <= 1:
            # Adapter wrote an _escalated marker → ESCALATE
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=0.8,
                reasoning="langmem-adapter: policy returned ESCALATE",
            )
        if len(non_escalated) > 1:
            # Multiple distinct memories persisted → semantically ESCALATE
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=0.8,
                reasoning="langmem-adapter: multiple writes preserved (ADD semantics)",
            )

        # Exactly one entry → pick winner_idx
        # Map back to scenario index by matching content text
        if non_escalated:
            (_sid, (_kind, content)) = next(iter(non_escalated.items()))
            kept_text = content if isinstance(content, str) else str(content.get("content", content))
            for idx, w in enumerate(scenario.writes):
                if kept_text == w.claim:
                    return Decision(
                        kind=DecisionKind.WINNER,
                        winner_idx=idx,
                        confidence=0.8,
                        reasoning=f"langmem-adapter: write {idx} retained in store",
                    )

        # Fallback: ESCALATE
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.0,
            reasoning="langmem-adapter: could not interpret final state",
        )
