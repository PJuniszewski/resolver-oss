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

## ⚠️ Honest verdict (2026-05-31): TypedMergePolicy does NOT generalize

**Three independent measurements converged: TypedMergePolicy loses to recency on real data.** Full writeup: [`docs/HONEST-VERDICT-2026-05-31.md`](docs/HONEST-VERDICT-2026-05-31.md).

| Measurement | Verdict |
|---|---|
| Predecessor project held-out (2026-05-30) | 1.000 dev → **0.650** real-data held-out (−35pp) |
| Full 60-scen agent-merge-bench × LangMem real `ainvoke` | stock 0.567 → typed **0.517** (−5pp, typed LOSES) |
| 27-scen real-data bench (LongMemEval-S extracted) | recency 0.778 vs typed **0.407** (recency beats typed by **+37pp**) |

The simulated +40pp deltas previously reported in this README **do not survive real-pipeline + real-data validation.** TypedMergePolicy's deterministic escalation on COMPLEMENTARY/TEMPORAL_SCOPE — which dominated the synthetic bench distribution — over-escalates on real data where the same patterns more often have a clear winner.

### What ships honestly

| Component | Status |
|---|---|
| `MergePolicy` Protocol + `MemoryWrite` / `MergeDecision` dataclasses | ✅ correct and reusable |
| `PassthroughPolicy`, `RecencyPolicy` | ✅ correct reference implementations |
| `TypedMergePolicy` | ⚠️ **reference impl with documented failure mode; not recommended for production.** Better to call `RecencyPolicy()` until a successor architecture is built. |
| 5 adapters (BenchAdapter, LangMemBenchAdapter, LangMemE2EAdapter, Mem0BenchAdapter, LettaBenchAdapter) | ✅ adapter pattern proven across 3 hosts |
| Real-data extraction script (`scripts/extract_from_longmemeval.py`) | ✅ replicable; 30 rows → 27 scenarios → $2 |
| The MERGE-POLICY-AS-PLUGIN hypothesis | ✅ proven — the hook is load-bearing infrastructure |
| The SPECIFIC POLICY shipped | ❌ does not beat recency on real data |

### Why this matters more than the loss

This is **research-grade convergent evidence** for a methodology lesson: architectures tuned implicitly against a synthetic bench overstate their benefit. Repeated, on a public dataset, across three independent adapter targets. It's the resolver-internal predecessor's failure mode reproduced with public artifacts — useful as a cautionary case study in any future memory-merge work.

The Protocol, harness, adapters, real-data extraction, and methodology survive. The included default policy does not. Both ship honestly.

## Honest about state

- v0.1.0-alpha. The Protocol is stable; the LLM specialist prompts are not finalized.
- TypedMergePolicy hit 0.967 on the [synthetic agent-merge-bench](https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/v0-leaderboard.md) (via `BenchAdapter`) and 0.900 when run through `LangMemBenchAdapter` (slight drop = adapter state→Decision mapping artifact). But the predecessor architecture **failed held-out validation in the predecessor project** (dev 1.000 → held-out 0.650). Same architecture; same caveat applies here.
- The LangMem adapter is a SIMULATION of the merge layer (bypasses extraction). Full end-to-end `MemoryStoreManager.ainvoke()` integration is v0.2.
- Multi-write (>2 writers) is not supported — pairwise only. v0.2 priority.
- Only one model tested (claude-sonnet-4-6). Cross-provider (GPT, Gemini) untested.
- Only one host (LangMem) integrated so far. Mem0, Letta, Cognee adapters are v0.2.

## License

Apache-2.0. Compatible with Mem0 (Apache-2.0), Graphiti (Apache-2.0), LangMem (MIT), Letta (various), Cognee (Apache-2.0).
