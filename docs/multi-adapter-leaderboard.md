# Multi-adapter leaderboard — resolver-oss × {LangMem, Mem0, Letta}

**Date:** 2026-05-31
**Scenarios:** 60 from [agent-merge-bench v0.1.0](https://github.com/PJuniszewski/agent-merge-bench) (MD5 `0c12779f41e057081c6283311185039d`).
**Model:** `claude-sonnet-4-6`.
**Status:** simulated merge-layer for all three; LangMem additionally has a real-`ainvoke` adapter (`LangMemE2EAdapter`) — see "End-to-end LangMem" section.

## Headline

| Adapter | Default Behavior | Acc | Over-res | Under-res | ECE |
|---|---|---:|---:|---:|---:|
| `langmem/stock` | last-write-wins (id collision) | 0.500 | 1.000 | 0.000 | 0.300 |
| `langmem/typed-merge` | policy hook injected | **0.900** | 0.100 | 0.075 | 0.123 |
| `mem0/stock` | 4-op LLM picks (≈ recency on conflict pairs) | 0.500 | 1.000 | 0.000 | 0.300 |
| `mem0/typed-merge` | policy hook adapts to 4-op | **0.850** | 0.100 | 0.150 | 0.117 |
| `letta/stock` | additive append (memory_insert) | 0.333 | 0.000 | 1.000 | 0.000 |
| `letta/typed-merge` | policy decides insert vs replace | **0.900** | 0.100 | 0.075 | 0.123 |

## Reading

### Stock behavior varies dramatically across hosts

- **LangMem & Mem0 stock both collapse to recency** on conflict pairs about the same subject. LangMem's `_apply_manager_output` does last-write-wins on stable_id collision; Mem0's LLM picks UPDATE (which overwrites). Both wrong 50% of the time on contradictions by construction (bench has 10 newer-true + 10 older-true). And both over-resolve 100% (never escalate complementary/temporal_scope).
- **Letta is the opposite extreme.** Its default `memory_insert` is APPENDING — both writes preserved, no merge. So on contradictions, refinements, duplicates (where ground-truth is WINNER), Letta gets 100% under-resolution. But on complementary + temporal_scope it correctly preserves both → over-res = 0.000. Net accuracy 0.333 (just the 20/60 escalate-expected scenarios that match its "always preserve both" behavior).

### TypedMergePolicy lifts all three to ~0.85-0.90 accuracy

- **+40pp on LangMem** (0.500 → 0.900)
- **+35pp on Mem0** (0.500 → 0.850)
- **+57pp on Letta** (0.333 → 0.900)
- **Over-resolution drops from 1.000 → 0.100 on LangMem/Mem0** (the dominant stock failure mode)
- **Under-resolution drops from 1.000 → 0.075 on Letta** (typed-merge knows when to overwrite vs append)
- **ECE drops** across the board (0.300/0.300/0.000 → 0.123/0.117/0.123) — even Letta which started at 0 ECE (always confident, never wrong on calibration because it never picks a winner) now has meaningful confidence track-record

### LangMem and Letta land at IDENTICAL numbers (0.900 / 0.100 / 0.075 / 0.123)

Both adapters route through the same TypedMergePolicy logic, and their host semantics differ in defaults but not in how typed decisions are applied (UPDATE/SUPERSEDE = overwrite; ADD = preserve both; DEDUPE = drop incoming; ESCALATE = both with marker). So once policy is in control, behavior converges.

### Mem0 is 5pp below LangMem+Letta on typed-merge

`mem0/typed-merge` lands 0.850 vs 0.900. Under-resolution is 0.150 vs 0.075 — 4-5 extra scenarios where policy ESCALATEd but Mem0's interpretation of "ESCALATED marker" produces a different bench Decision than LangMem/Letta's. This is interpretation-layer artifact, not policy difference: Mem0 has no native ESCALATE action, so the adapter creates a sentinel marker entry that the bench's heuristic sometimes reads differently. The policy IS doing the same thing across all three; the SCORING differs because the bench projection of "what's in the store after" is per-host.

### Polymorphism is the real result

Same `TypedMergePolicy` instance → +35-57pp accuracy across three architecturally distinct hosts (last-write-wins, LLM-pick-op, additive-append). That's the central claim of `resolver-oss`: the policy is portable; the adapters do the host-specific translation.

## End-to-end LangMem (real `ainvoke` pipeline)

In addition to the simulated merge-layer adapter above, `resolver-oss` ships `LangMemE2EAdapter` which runs the FULL `MemoryStoreManager.ainvoke()` pipeline (real extraction via trustcall + real retrieval + merge). The monkey-patch path injects the policy into `_apply_manager_output` for typed mode.

See `docs/langmem-e2e-results-2026-05-31.md` for full e2e numbers (run separately; results pending at time of this writeup — runs $7-10 in API).

The simulation results above SHOULD be treated as upper bounds; real ainvoke includes extraction-step noise that the simulation skips.

## Honest caveats (apply to all three adapters)

1. **All three are SIMULATIONS of the merge layer.** Stock behavior is approximated based on documented semantics, not measured against real host deployments.
2. **Bench distribution may not reflect production traffic.** Held-out validation in the predecessor project (resolver-internal) crashed the same architecture to 0.650 on real-data; +35-57pp deltas here are an upper bound.
3. **One model only** (claude-sonnet-4-6). Cross-provider untested.
4. **Pairwise only.** Multi-write (>2 writers) scenarios — common in real multi-agent setups — not supported in v0.1.0.
5. **Inter-labeller on this bench is 0.929 type / 1.000 decision** (see [agent-merge-bench's inter-labeller report](https://github.com/PJuniszewski/agent-merge-bench/blob/main/docs/inter-labeller-2026-05-31.md)). One scenario (ts-10) is mis-typed; decision-level numbers above are unaffected.

## Reproducing

```bash
git clone https://github.com/PJuniszewski/resolver-oss.git
cd resolver-oss
make install
.venv/bin/pip install -e ../01-agent-merge-bench

export ANTHROPIC_API_KEY=sk-ant-...
python -c "
from resolver_oss import TypedMergePolicy
from resolver_oss.adapters.langmem import LangMemBenchAdapter
from resolver_oss.adapters.mem0 import Mem0BenchAdapter
from resolver_oss.adapters.letta import LettaBenchAdapter
from agent_merge_bench.schema import load_benchmark
from agent_merge_bench.harness import evaluate

scenarios = load_benchmark('../01-agent-merge-bench/scenarios/benchmark.json')
for AdCls in [LangMemBenchAdapter, Mem0BenchAdapter, LettaBenchAdapter]:
    for policy in [None, TypedMergePolicy()]:
        a = AdCls(policy=policy)
        e = evaluate(a, scenarios)
        print(f'{a.name:<45} acc={e.accuracy:.3f} over={e.over_resolution_rate:.3f} ece={e.ece:.3f}')
"
```

Total cost: ~$2-3 in API spend (TypedMergePolicy responses cached after first run; cache hits across adapter runs).
