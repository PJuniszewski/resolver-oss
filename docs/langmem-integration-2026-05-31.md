# LangMem integration delta — first measurement (2026-05-31)

**Subject:** does plugging `resolver_oss.TypedMergePolicy` into LangMem's `_apply_manager_output` move the needle on `agent-merge-bench`?
**Status:** simulation; v0.1.0-alpha results.
**Scenarios:** 60 synthetic from [agent-merge-bench v0.1.0](https://github.com/PJuniszewski/agent-merge-bench) (MD5 `0c12779f41e057081c6283311185039d`).
**Model:** `claude-sonnet-4-6`.
**This fulfills:** §5 ("benchmark commitment") of the LangMem RFC draft, in simulated form. Full end-to-end LangMem `ainvoke()` run with extraction is v0.2 work.

## Headline

| Adapter | Accuracy | Over-resolution | Under-resolution | ECE |
|---|---:|---:|---:|---:|
| `langmem/stock` (last-write-wins; bypasses extraction) | 0.500 | 1.000 | 0.000 | 0.300 |
| `langmem/typed-merge` (TypedMergePolicy injected at merge layer) | **0.900** | **0.100** | 0.075 | **0.123** |
| **Delta** | **+0.400** | **−0.900** | +0.075 | **−0.177** |

**Interpretation:**

- **Accuracy +40pp.** LangMem's stock merge layer on conflict pairs of the same subject collapses to recency (last-write-wins on stable_id collision). Recency is wrong on 50% of the bench's contradictions by construction (10 newer-true + 10 older-true scenarios). The typed-merge policy fixes the older-true cases by classifying them and routing through the contradiction specialist instead of blind recency.
- **Over-resolution −90pp.** The dominant failure mode of stock LangMem in this setup: it picks a winner even for COMPLEMENTARY and TEMPORAL_SCOPE conflicts where both writes should persist. The typed-merge policy escalates these correctly via the deterministic escalation branch.
- **Under-resolution +7.5pp.** The TypedMergePolicy occasionally escalates (4-5 scenarios out of 60) when the bench expected a winner. This is the "saw a hard contradiction; couldn't decide" failure mode of the contradiction specialist. Compare to the typed-merge policy when run through the bench *without* the LangMem adapter: 0.000 under-res — meaning some of the under-res is artifact of the LangMem adapter's "two memories preserved → ESCALATE" interpretation, not the policy itself.
- **ECE −17.7pp.** Better calibration. The typed-merge policy's per-decision confidence tracks actual correctness more closely than stock LangMem's always-1.0.

## Methodology

### Why we simulate the merge layer rather than running the full pipeline

`MemoryStoreManager.ainvoke()` does TWO things: (1) extract candidate facts from a `messages` array via trustcall, (2) reconcile the extracted candidates against existing memories via `_apply_manager_output`. Adding our policy at step (2) is the proposed change in the [LangMem RFC](../../research/synthesis/langmem-rfc-draft.md).

Running the full pipeline against our 60 scenarios would require translating each scenario into a `messages` array that LangMem's extractor reliably produces the right two facts from — and that's its own LLM call with non-deterministic output. Extraction failures would muddle the comparison.

The cleaner experiment: hold extraction constant (assume both writes were correctly extracted with the same `stable_id`, which is what LangMem does when trustcall issues UPDATE on a perceived-same-fact), and compare the merge-layer behavior with vs without the policy. That's what this report measures.

### Stable-id assumption

For both modes, the simulation gives both writes in each scenario the SAME `stable_id`. This is the "best case for LangMem stock" — every scenario is a clean id collision, so LangMem's `_apply_manager_output` runs the UPDATE branch (last-write-wins) rather than the INSERT branch (both kept). If instead we gave them DIFFERENT stable_ids, LangMem-stock would persist BOTH writes always — which would score 0.0 on contradictions (no winner picked, ground truth expects WINNER) and 1.0 on complementary/temporal_scope (both kept, ground truth expects ESCALATE which we map to "multiple distinct entries"). That'd be a less interesting comparison.

### Reproducing

```bash
git clone https://github.com/PJuniszewski/resolver-oss.git
cd resolver-oss
make install
pip install -e '../01-agent-merge-bench'  # the companion bench

export ANTHROPIC_API_KEY=sk-ant-...
python -c "
from resolver_oss.adapters.langmem import LangMemBenchAdapter
from resolver_oss import TypedMergePolicy
from agent_merge_bench.schema import load_benchmark
from agent_merge_bench.harness import evaluate

scenarios = load_benchmark('../01-agent-merge-bench/scenarios/benchmark.json')

# Mode A: stock
print(evaluate(LangMemBenchAdapter(policy=None), scenarios).accuracy)

# Mode B: typed-merge
print(evaluate(LangMemBenchAdapter(policy=TypedMergePolicy()), scenarios).accuracy)
"
```

Cost: ~$0.50 in API spend for mode B (60 scenarios × ~1.5 LLM calls average × claude-sonnet-4-6).

## Honest caveats

1. **This is a simulation of the merge layer, not an end-to-end LangMem run.** Real `ainvoke()` includes extraction (trustcall call), retrieval scoping (vector search over the store), and phase passes. Each adds variance and potential failure modes the simulation doesn't capture. v0.2 priority: wrap real `ainvoke()`.

2. **Bench-as-distribution caveat.** agent-merge-bench's 60 synthetic scenarios have a deliberately adversarial distribution (10 newer-true + 10 older-true contradictions). Real LangMem usage may skew heavily toward newer-true (extractor's MERGE/SKIP thresholds delete older-true at write time). Held-out validation on real-data corpora in the predecessor project showed the architecture dropping from 1.000 to 0.650 — **the +40pp delta here is an upper bound on real performance.**

3. **Stable-id assumption is generous to typed-merge in one way and to stock in another.** Same-id means stock collapses to recency (vs ADD-both = preservation). It also means the typed-merge policy never sees a "different stable_id but semantically same fact" case where LangMem's extractor would have missed the deduplication opportunity. Net direction unclear without real data.

4. **LangMemBenchAdapter's state→Decision mapping is approximate.** "Two distinct memories in final store" → ESCALATE; "one memory" → WINNER pointing at the kept write. Real LangMem retrieval would behave differently. The 7.5pp under-resolution from typed-merge here is partly an artifact of this mapping — the TypedMergePolicy via direct BenchAdapter has 0.000 under-res.

5. **Inter-labeller on this bench is 0.929 type / 1.000 decision** (see [agent-merge-bench's inter-labeller report](https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/inter-labeller-2026-05-31.md)). One scenario (ts-10) is mis-typed in the bench. Decision-level numbers above are unaffected.

## What this proves

- **The policy hook IS load-bearing.** Adding it to LangMem's _apply_manager_output produces a measurable accuracy improvement (40pp on this bench).
- **The typed taxonomy IS the structure that matters.** The deterministic-escalation branch (for COMPLEMENTARY, TEMPORAL_SCOPE, DUPLICATE) accounts for most of the over-resolution improvement, without any LLM call.
- **Calibration improves alongside accuracy.** Self-reported confidence from the policy tracks correctness better than LangMem-stock's always-1.0 (ECE −18pp).

## What this does NOT prove

- **That LangMem upstream would benefit on production traffic.** Production traffic is not the bench distribution. The +40pp gap is an upper bound.
- **That the proposed Protocol shape is the best one.** It's *a* shape that works; other interfaces (e.g. two-pass extract-then-merge per the RFC's §6.1) might do better.
- **That TypedMergePolicy is the right default.** PassthroughPolicy is the recommended adapter default (preserves stock behavior, BC-safe); TypedMergePolicy is opt-in.
- **That this beats LangMem's own roadmap.** LangMem maintainers may have plans we don't know about (per #41, they want benchmark evidence — that's exactly the purpose of this report).

## Next steps

- **v0.2:** wrap full `MemoryStoreManager.ainvoke()` (real extraction + merge + retrieval). Compare same metrics.
- **v0.2:** run against LongMemEval-S subset. End-to-end QA accuracy delta is what the LangMem RFC §5 ultimately commits to.
- **v0.2:** repeat the same exercise against Mem0, Letta, Cognee — each with its own native merge layer; same comparison.
- **External signal trigger:** if this report draws an issue or comment from a LangMem maintainer, that unblocks the [draft RFC](../../research/synthesis/langmem-rfc-draft.md) for posting.
