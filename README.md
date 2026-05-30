# resolver-oss

> Typed semantic conflict resolution for multi-agent memory — a pluggable merge-policy library for memory stacks like LangMem, Mem0, Letta, Cognee.

**Status:** v0.1.0-alpha — API stable enough to integrate against, implementation in active validation. Companion benchmark at [agent-merge-bench](https://github.com/PJuniszewski/agent-merge-bench).

## What this is

When two agents write into shared memory and disagree, today's memory stacks pick "winner by recency" or "keep both with retrieval-time scoring" or "single LLM judge with a 4-op vocabulary." None distinguishes between conflict TYPES and applies type-appropriate logic.

This library provides a **`MergePolicy` Protocol** + 3 reference implementations + adapter contracts. Drop it into your memory stack's pre-commit hook to get typed merges, calibrated confidence, and an explicit ESCALATE path.

## The 5-action vocabulary

| Action | When | Effect |
|---|---|---|
| `ADD` | Different facet of same entity (complementary) | Insert; don't touch existing |
| `UPDATE` | Incoming strictly more specific (refinement) | Replace target with incoming |
| `DEDUPE` | Same fact, different phrasing | Drop incoming; keep existing |
| `SUPERSEDE_BY_TIME` | Later observation of mutated state | Replace target; optionally record lineage |
| `ESCALATE` | Can't confidently resolve | Surface to caller; don't auto-commit |

Maps to the [5-type conflict taxonomy](https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/typed-conflict-taxonomy.md): contradiction → SUPERSEDE_BY_TIME or ESCALATE; refinement → UPDATE; complementary → ADD or ESCALATE; temporal_scope → ESCALATE; duplicate → DEDUPE.

## Install

```bash
# From source (PyPI publication pending):
git clone https://github.com/PJuniszewski/resolver-oss.git
cd resolver-oss
make install

# With LLM extras (for TypedMergePolicy):
pip install -e '.[llm]'

# With LangMem adapter:
pip install -e '.[langmem]'
```

## Usage

```python
from resolver_oss import (
    MergePolicy, MemoryWrite, MergeDecision,
    TypedMergePolicy, PassthroughPolicy, RecencyPolicy,
)

# Build the policy
policy: MergePolicy = TypedMergePolicy()  # uses claude-sonnet-4-6 + ANTHROPIC_API_KEY

# Use it inside your store's commit hook
incoming = MemoryWrite(id="mem-42", kind="Memory", content="user prefers Krakow")
existing = [MemoryWrite(id="mem-7", kind="Memory", content="user lives in Warsaw")]
decision: MergeDecision = policy.handle(incoming, existing)

# decision.action ∈ {ADD, UPDATE, DEDUPE, SUPERSEDE_BY_TIME, ESCALATE}
# decision.target_id — which existing write to act on (for UPDATE/DEDUPE/SUPERSEDE)
# decision.confidence — float [0,1], for ECE tracking
```

## Reference implementations

- **`TypedMergePolicy`** — the headline one. LLM classifier (1 call) + per-type specialist (1 call) + deterministic escalation for complementary / temporal_scope / duplicate. ~2 LLM calls per resolve worst case. Default model `claude-sonnet-4-6`.
- **`PassthroughPolicy`** — preserves upstream system's default behavior (UPDATE on id collision, ADD otherwise). Use as adapter default for backward compatibility.
- **`RecencyPolicy`** — newest-wins baseline. SUPERSEDE_BY_TIME against the most-recent existing.

## Honest about state

- v0.1.0-alpha. The Protocol is stable; the LLM specialist prompts are not finalized.
- TypedMergePolicy hit 0.967 on the [synthetic agent-merge-bench](https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/v0-leaderboard.md), but the predecessor architecture **failed held-out validation in the predecessor project** (dev 1.000 → held-out 0.650). Same architecture; same caveat applies here. Treat single-bench numbers as upper bounds.
- The library has NO integration tests against a real memory system yet. LangMem adapter is shipping in v0.1.x; until then, this is validated only via the bench's reference implementations.
- Multi-write (>2 writers) is not supported — pairwise only. v0.2 priority.
- Only one model tested (claude-sonnet-4-6). Cross-provider (GPT, Gemini) untested.

## License

Apache-2.0. Compatible with Mem0 (Apache-2.0), Graphiti (Apache-2.0), LangMem (MIT), Letta (various), Cognee (Apache-2.0).
