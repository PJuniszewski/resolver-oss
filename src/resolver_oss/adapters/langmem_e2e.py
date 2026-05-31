"""End-to-end LangMem adapter — runs the FULL MemoryStoreManager.ainvoke pipeline.

Difference from `langmem.LangMemAdapter` (simulation):
- That one bypassed extraction; only simulated the merge layer
- This one runs real ainvoke: extraction (trustcall LLM call) + retrieval + merge

Trade-off:
- More credible: tests what would actually happen on real LangMem deployments
- More expensive: ~3-4 LLM calls per scenario vs ~1-2 for the simulation
- Noisier: extraction may not extract the exact facts we labeled

The monkey-patch path:
- `langmem.knowledge.extraction.MemoryStoreManager._apply_manager_output` is a static method.
- For "with policy" mode we replace it with a wrapper that consults `policy.handle()`
  before falling back to the original logic.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..policy import MemoryWrite, MergePolicy

if TYPE_CHECKING:
    from agent_merge_bench.schema import Decision, Scenario


def _writes_to_messages(writes: list) -> list[dict]:
    """Translate bench Writes → LangChain messages for LangMem extraction.

    Each write becomes a `user` turn with a `[author at timestamp] claim` format.
    LangMem's trustcall extractor should pick up the fact from each turn.
    """
    msgs = []
    for w in writes:
        msgs.append({
            "role": "user",
            "content": f"[{w.author} at {w.timestamp}] {w.claim}",
        })
    return msgs


@contextlib.contextmanager
def _patched_apply(policy: MergePolicy | None):
    """Monkey-patch MemoryStoreManager._apply_manager_output for the duration.

    If policy is None, this is a no-op (stock behavior). If set, every merge
    decision is routed through the policy before commit.
    """
    if policy is None:
        yield
        return

    from langmem.knowledge.extraction import MemoryStoreManager
    from pydantic import BaseModel

    original = MemoryStoreManager._apply_manager_output

    @staticmethod
    def patched(
        manager_output,
        store_based,
        store_map,
        ephemeral,
    ):
        # Build existing MemoryWrite views (from store_based + ephemeral)
        existing_mws: list[MemoryWrite] = []
        for sid, kind, content in store_based:
            existing_mws.append(MemoryWrite(
                id=sid, kind=kind,
                content=content if isinstance(content, (dict, str)) else str(content),
            ))
        for sid, kind, content in ephemeral:
            existing_mws.append(MemoryWrite(
                id=sid, kind=kind,
                content=content if isinstance(content, (dict, str)) else str(content),
            ))

        store_dict = {sid: (sid, kind, content) for (sid, kind, content) in store_based}
        ephemeral_dict = {sid: (sid, kind, content) for (sid, kind, content) in ephemeral}
        removed_ids: list[str] = []

        for extracted in manager_output:
            stable_id = extracted.id
            model_data = extracted.content

            # RemoveDoc path — preserve stock behavior
            if isinstance(model_data, BaseModel):
                if hasattr(model_data, "__repr_name__") and model_data.__repr_name__() == "RemoveDoc":
                    removal_id = getattr(model_data, "json_doc_id", None)
                    if removal_id and removal_id in store_map:
                        removed_ids.append(removal_id)
                    store_dict.pop(removal_id, None)
                    ephemeral_dict.pop(removal_id, None)
                    continue
                new_content = model_data.model_dump(mode="json")
                new_kind = model_data.__repr_name__()
            else:
                new_kind = store_dict.get(stable_id, (stable_id, "Memory", {}))[1]
                new_content = model_data

            # Build the incoming MemoryWrite for the policy
            incoming = MemoryWrite(
                id=stable_id, kind=new_kind,
                content=new_content if isinstance(new_content, (dict, str)) else str(new_content),
            )

            # Consult policy
            try:
                decision = policy.handle(incoming, existing_mws)
            except Exception:
                # Fail-safe: preserve original behavior
                if stable_id in store_dict:
                    store_dict[stable_id] = (stable_id, new_kind, new_content)
                else:
                    ephemeral_dict[stable_id] = (stable_id, new_kind, new_content)
                continue

            if decision.action == "ADD":
                ephemeral_dict[stable_id] = (stable_id, new_kind, new_content)
            elif decision.action in ("UPDATE", "SUPERSEDE_BY_TIME"):
                tid = decision.target_id or stable_id
                if tid in store_dict:
                    store_dict[tid] = (tid, new_kind, new_content)
                elif tid in ephemeral_dict:
                    ephemeral_dict[tid] = (tid, new_kind, new_content)
                else:
                    ephemeral_dict[stable_id] = (stable_id, new_kind, new_content)
            elif decision.action == "DEDUPE":
                # Drop incoming; keep existing as-is
                pass
            elif decision.action == "ESCALATE":
                # Write with marker — adapter caller observes via final state
                marked = (
                    {"_escalated": True, "content": new_content}
                    if isinstance(new_content, str)
                    else {**new_content, "_escalated": True}
                )
                ephemeral_dict[f"{stable_id}-esc"] = (f"{stable_id}-esc", new_kind, marked)
            else:
                # Unknown action — preserve original
                if stable_id in store_dict:
                    store_dict[stable_id] = (stable_id, new_kind, new_content)
                else:
                    ephemeral_dict[stable_id] = (stable_id, new_kind, new_content)

        return list(store_dict.values()), list(ephemeral_dict.values()), removed_ids

    MemoryStoreManager._apply_manager_output = patched
    try:
        yield
    finally:
        MemoryStoreManager._apply_manager_output = original


class LangMemE2EAdapter:
    """End-to-end LangMem adapter for the bench's Resolver protocol.

    Runs `MemoryStoreManager.ainvoke()` twice per scenario (one per write,
    sequential — second sees first as 'existing' via store search).
    """

    def __init__(
        self,
        policy: MergePolicy | None = None,
        *,
        model: str = "claude-sonnet-4-6",
        name_suffix: str = "",
    ) -> None:
        self.policy = policy
        self.model = model
        policy_name = getattr(policy, "name", "stock") if policy else "stock"
        self.name = f"langmem-e2e/{policy_name}{name_suffix}"

    def resolve(self, scenario: "Scenario") -> "Decision":
        from agent_merge_bench.schema import Decision, DecisionKind

        return asyncio.run(self._resolve_async(scenario, Decision, DecisionKind))

    async def _resolve_async(self, scenario, Decision, DecisionKind):
        from langgraph.store.memory import InMemoryStore
        from langmem import create_memory_store_manager
        from langchain_core.runnables import RunnableConfig

        # Use a per-scenario namespace tuple to isolate stores
        ns_id = scenario.id.replace("-", "_")
        store = InMemoryStore()
        manager = create_memory_store_manager(
            self.model,
            namespace=("memories", ns_id),
            store=store,
            enable_inserts=True,
            enable_deletes=False,  # avoid noise from deletes
            query_limit=10,
        )

        config: RunnableConfig = {"configurable": {}}

        try:
            with _patched_apply(self.policy):
                # Round 1: write 1
                msgs1 = _writes_to_messages([scenario.writes[0]])
                await manager.ainvoke({"messages": msgs1, "existing": []}, config=config)
                # Round 2: write 2 (existing memories from round 1 will be retrieved
                # by MemoryStoreManager's internal search)
                msgs2 = _writes_to_messages([scenario.writes[1]])
                await manager.ainvoke({"messages": msgs2, "existing": []}, config=config)
        except Exception as e:
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=0.0,
                reasoning=f"langmem-e2e error: {type(e).__name__}: {str(e)[:80]}",
            )

        # Inspect final store state
        items = await store.asearch(("memories", ns_id))
        return _interpret_final_state(items, scenario, Decision, DecisionKind)


def _extract_text(value: Any) -> str:
    """Pull text out of a LangMem store value (handles nested {'content': {'content': ...}})."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        c = value.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, dict):
            return _extract_text(c)
        return str(value)
    return str(value)


def _winner_by_distinguishing_tokens(text: str, writes: list) -> int | None:
    """Pick the write whose UNIQUE tokens appear in `text`.

    Compares writes pairwise: find tokens in writes[i] that are NOT in writes[j],
    count how many of those appear in `text`. Higher count = closer match.
    Falls back to substring containment of full claims if symmetric-difference
    tokens don't disambiguate.
    """
    text_lower = text.lower()
    text_tokens = set(text_lower.split())

    n = len(writes)
    if n < 2:
        return 0 if n == 1 else None

    # For each write, count its UNIQUE tokens vs the OTHER writes that appear in text
    scores = []
    for i in range(n):
        unique_to_i = set(writes[i].claim.lower().split())
        for j in range(n):
            if j != i:
                unique_to_i -= set(writes[j].claim.lower().split())
        # Also filter generic stopwords-like tokens that aren't distinguishing
        unique_to_i -= {"the", "a", "an", "is", "at", "in", "on", "for", "to", "of", "and", "or", "but", "by"}
        score = sum(1 for tok in unique_to_i if tok in text_tokens)
        scores.append((score, len(unique_to_i)))

    # Pick the write whose unique-token-presence ratio is highest
    best_idx = None
    best_ratio = -1.0
    for i, (score, total) in enumerate(scores):
        if total == 0:
            continue  # no unique tokens; skip
        ratio = score / total
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i

    if best_idx is not None and best_ratio > 0.2:
        return best_idx

    # Fallback: substring containment of any write's claim
    for i, w in enumerate(writes):
        if w.claim.lower() in text_lower:
            return i

    # Last resort: SequenceMatcher
    import difflib
    sims = [difflib.SequenceMatcher(None, text_lower, w.claim.lower()).ratio() for w in writes]
    return max(range(n), key=lambda i: sims[i])


def _interpret_final_state(items, scenario, Decision, DecisionKind):
    """Map final LangMem store state → bench Decision."""
    non_escalated = []
    escalated_count = 0
    for item in items:
        v = item.value
        text = _extract_text(v)
        # An item is "escalated" if the underlying dict has _escalated=True at any nesting
        is_esc = False
        if isinstance(v, dict):
            if v.get("_escalated"):
                is_esc = True
            elif isinstance(v.get("content"), dict) and v["content"].get("_escalated"):
                is_esc = True
        if is_esc:
            escalated_count += 1
        else:
            non_escalated.append((item, text))

    # If escalation marker present → ESCALATE
    if escalated_count > 0 and len(non_escalated) <= 1:
        return Decision(
            kind=DecisionKind.ESCALATE,
            confidence=0.7,
            reasoning="langmem-e2e: policy returned ESCALATE",
        )

    # Multiple distinct non-escalated memories → both kept → ESCALATE semantically
    if len(non_escalated) >= 2:
        # Check if the multiple memories actually represent DIFFERENT writes (additive)
        # vs DUPLICATES (same content stored twice). Use distinguishing tokens.
        winners_per_memory = [
            _winner_by_distinguishing_tokens(text, scenario.writes)
            for _item, text in non_escalated
        ]
        distinct_winners = set(w for w in winners_per_memory if w is not None)
        if len(distinct_winners) >= 2:
            # Multiple memories representing different writes → additive → ESCALATE
            return Decision(
                kind=DecisionKind.ESCALATE,
                confidence=0.7,
                reasoning=f"langmem-e2e: {len(non_escalated)} memories preserved, "
                          f"covering writes {sorted(distinct_winners)}",
            )
        elif len(distinct_winners) == 1:
            idx = next(iter(distinct_winners))
            return Decision(
                kind=DecisionKind.WINNER,
                winner_idx=idx,
                confidence=0.7,
                reasoning=f"langmem-e2e: {len(non_escalated)} memories all match write {idx}",
            )

    # Single memory persisted — pick winner_idx by content matching
    if len(non_escalated) == 1:
        _item, text = non_escalated[0]
        best_idx = _winner_by_distinguishing_tokens(text, scenario.writes)
        if best_idx is not None:
            return Decision(
                kind=DecisionKind.WINNER,
                winner_idx=best_idx,
                confidence=0.7,
                reasoning=f"langmem-e2e: single memory matches write {best_idx}",
            )

    # Fallback
    return Decision(
        kind=DecisionKind.ESCALATE,
        confidence=0.0,
        reasoning="langmem-e2e: could not interpret final state",
    )
