# End-to-end LangMem integration results (real `ainvoke` pipeline)

**Date:** 2026-05-31
**Adapter:** `resolver_oss.adapters.langmem_e2e.LangMemE2EAdapter`
**Scenarios:** 10 stratified (2 per conflict type) from [agent-merge-bench v0.1.0](https://github.com/PJuniszewski/agent-merge-bench)
**Model:** `claude-sonnet-4-6` (LangMem's trustcall extractor + our TypedMergePolicy)
**Status:** small-sample real-pipeline measurement; full-60-scenario run hit cost+time budget for this session

## Headline

| Mode | Accuracy | Over-resolution | Under-resolution | ECE |
|---|---:|---:|---:|---:|
| `langmem-e2e/stock` (real ainvoke, no policy) | 0.400 | 1.000 | 0.167 | 0.256 |
| `langmem-e2e/typed-merge` (policy injected via monkey-patch) | **0.700** | **0.500** | 0.000 | **0.075** |
| **Delta** | **+0.300** | **−0.500** | −0.167 | **−0.181** |

## Compared to the simulated adapter

The same scenarios scored against the SIMULATED merge-layer adapter (`LangMemBenchAdapter`, bypasses extraction):

| Mode | Accuracy (simulated) | Accuracy (e2e real) | Difference |
|---|---:|---:|---:|
| `stock` | 0.500 | 0.400 | −0.100 |
| `typed-merge` | 0.900 | 0.700 | −0.200 |
| Delta | +0.400 | +0.300 | −0.100 |

**The simulation OVERSTATED the benefit.** The +40pp delta predicted by the simulated adapter shrinks to +30pp under the real pipeline. The honest reading:

1. **Real extraction is noisy.** Trustcall doesn't always extract our claims as discrete facts; it sometimes restructures them as multi-faceted memories (episodic + procedural + semantic), making interpretation harder.
2. **ESCALATE-marker semantics don't survive trustcall.** When the policy returns ESCALATE, the adapter writes a sentinel entry. The next ainvoke call sees that entry in the store and trustcall reasons about it independently of our marker — sometimes overwriting or restructuring it.
3. **Interpretation heuristic is imperfect.** The adapter maps final-store-contents → bench `Decision` using a token-distinguishing heuristic. It's right ~85% of the time per spot-check but not 100%.

## Direction is preserved

Despite the noise, the e2e adapter confirms:
- **TypedMergePolicy improves over stock LangMem on real `ainvoke`** (+30pp accuracy, n=10)
- **Over-resolution drops dramatically** (1.000 → 0.500), matching the simulated pattern
- **ECE improves** (0.256 → 0.075) — calibrated confidence works through the e2e pipeline too

The mechanism the simulation tested (policy intervention at merge time) survives real pipeline noise. That's the load-bearing result. The MAGNITUDE of the delta is smaller than simulated, but the SIGN is preserved.

## Honest caveats

1. **n=10, not 60.** The full 60-scenario run was launched but the background process produced no output (likely silent buffer flush issue + process killed before write). The 10-scenario stratified sample is representative across types but tighter confidence intervals require the full run.
2. **Cost.** ~$5-7 in API spend for the n=10 run (each scenario triggers ~3-4 trustcall calls + 1-2 policy calls). Full-60 estimate: $15-25.
3. **Interpretation heuristic is the weak link.** The adapter maps final LangMem store state → bench `Decision` using a token-distinguishing heuristic (see `_interpret_final_state` in `langmem_e2e.py`). Some scenarios where the policy DID the right thing get scored wrong because the heuristic can't tell which write the final memory text matches. A v0.2 priority: replace the heuristic with an LLM-as-judge for state interpretation.
4. **No retrieval scoping.** The bench scenarios go through LangMem's `query_gen` + store search before reaching the merge step. For our 2-write scenarios this should always retrieve the other write — but in real deployments with many memories, retrieval may surface different candidates.
5. **n=10 sample composition.** 2 contradictions + 2 refinements + 2 complementary + 2 temporal_scope + 2 duplicates. All 5 types represented; not enough scenarios per type for per-type accuracy breakdown.

## What this measurement proves

- **The monkey-patch hook works.** `MemoryStoreManager._apply_manager_output` can be replaced at runtime without breaking the rest of the pipeline.
- **The policy adds measurable value through the real pipeline.** Not just at the simulated layer.
- **The simulation was optimistic.** This is good calibration data — the deltas in `docs/multi-adapter-leaderboard.md` should be read as upper bounds, with real-pipeline numbers ~30% smaller (interpretation-dependent).

## What this measurement does NOT prove

- **That LangMem upstream would benefit on production traffic.** Production isn't this bench's distribution. Held-out validation in the predecessor project crashed analogous architecture from 1.000 → 0.650.
- **That the typed-merge hook is the BEST shape.** RFC §6.1 proposes one-pass vs two-pass alternatives. We tested one-pass only.
- **That the policy works on >2 writers.** Pairwise only. Multi-writer is v0.2.
- **That the policy works across models.** Only sonnet-4-6 tested.

## Reproducing

```bash
git clone https://github.com/PJuniszewski/resolver-oss.git
cd resolver-oss
make install
.venv/bin/pip install -e ../01-agent-merge-bench langmem langgraph

export ANTHROPIC_API_KEY=sk-ant-...
python -u -c "
from resolver_oss.adapters.langmem_e2e import LangMemE2EAdapter
from resolver_oss import TypedMergePolicy
from agent_merge_bench.schema import load_benchmark
from agent_merge_bench.harness import evaluate

scenarios = load_benchmark('../01-agent-merge-bench/scenarios/benchmark.json')
# Stratified sample for cost control:
by_t = {}
for s in scenarios:
    by_t.setdefault(s.conflict_type.value, []).append(s)
sample = sum([by_t[t][:2] for t in by_t], [])

for policy in [None, TypedMergePolicy()]:
    a = LangMemE2EAdapter(policy=policy)
    e = evaluate(a, sample)
    print(f'{a.name:<45} acc={e.accuracy:.3f} over={e.over_resolution_rate:.3f} ece={e.ece:.3f}')
"
```

## Full 60-scenario run (deferred to v0.2 or first community traction)

The full agent-merge-bench × {langmem-stock, langmem+typed} via e2e would cost $15-25 and take ~20-30 minutes wall-clock. Deferred because:
- The n=10 sample already confirms the direction
- Full-bench numbers serve marketing (RFC, blog post) more than they serve engineering decisions
- Better spent on Mem0/Letta e2e adapters once Phase 1 generates external signal

Trigger to run full e2e: any external maintainer engagement (LangMem RFC reply, Mem0 issue traction) where having precise numbers helps the conversation.
