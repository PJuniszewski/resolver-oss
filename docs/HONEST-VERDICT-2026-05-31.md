# HONEST VERDICT — TypedMergePolicy does not generalize (2026-05-31)

**Status:** retraction-grade finding. Supersedes optimistic claims in `multi-adapter-leaderboard.md` and `langmem-integration-2026-05-31.md`.

This document consolidates three INDEPENDENT measurements made over 2026-05-30 → 2026-05-31, all of which converge on the same conclusion: **the TypedMergePolicy architecture, while it scores 0.967 on the synthetic agent-merge-bench, does not generalize to real-data or to full-pipeline integration.**

## The three measurements

### 1. Predecessor project held-out (resolver-internal, 2026-05-30)

| Setup | Result |
|---|---:|
| Routed pipeline on synthetic dev bench (60 scenarios, same architecture) | **1.000** |
| Routed pipeline on held-out real-data bench (20 scenarios, Nexos staging) | **0.650** |

**Drop: −35pp.** Architecture explicitly tuned for the dev bench; crashed on a separately-constructed real-data bench. Full report: `~/juni-labs/projects/resolver/research/synthesis/held-out-verdict-2026-05.md`.

### 2. End-to-end LangMem pipeline (this session, 2026-05-31)

LangMem-stock vs LangMem+TypedMergePolicy on full 60-scenario agent-merge-bench, run through real `MemoryStoreManager.ainvoke()`:

| Mode | Accuracy | Over-res | Under-res | ECE |
|---|---:|---:|---:|---:|
| `langmem-e2e/stock` | 0.567 | 0.950 | 0.000 | 0.141 |
| `langmem-e2e/typed-merge` | **0.517** | 0.900 | 0.125 | 0.153 |
| **Delta** | **−0.050** | −0.050 | +0.125 | +0.012 |

**TypedMergePolicy slightly LOSES to stock LangMem when run through real ainvoke on the full 60 scenarios.** Earlier n=10 stratified sample suggested +30pp; that was sampling-lucky. Full-bench result is the honest one.

### 3. LongMemEval-S-derived real-data bench (this session, 2026-05-31)

Real conflict pairs extracted via LLM from the LongMemEval-S `cleaned` HuggingFace dataset (27 scenarios extracted from 30 LongMemEval rows). Distribution: 11 complementary / 8 contradiction / 5 refinement / 2 duplicate / 1 temporal_scope.

| Adapter | Accuracy on REAL data | Accuracy on SYNTHETIC | Difference |
|---|---:|---:|---:|
| `recency` (direct) | **0.778** | 0.500 | +0.278 |
| `typed-merge` (direct) | 0.407 | 0.967 | **−0.560** |
| `langmem/stock` (sim) | 0.778 | 0.500 | +0.278 |
| `langmem/typed-merge` (sim) | 0.333 | 0.900 | **−0.567** |
| `mem0/typed-merge` (sim) | 0.407 | 0.850 | −0.443 |
| `letta/typed-merge` (sim) | 0.407 | 0.900 | −0.493 |

**Recency BEATS TypedMergePolicy on real data across every adapter, by 27-44pp.** TypedMergePolicy's under-resolution rate is 0.571 (57% of winner-expected cases incorrectly escalated). The deterministic-escalation branch for COMPLEMENTARY (which dominated the real-data distribution at 41% of scenarios) is biased toward escalation when the real-world correct answer is often a specific winner.

## The convergent diagnosis

All three measurements point at the same failure mode:

**TypedMergePolicy was tuned (implicitly, via the bench scenarios its predecessor was designed against) to over-escalate on cases that LOOK complementary or temporal_scope. On real data those same patterns more often have a clear winner (the user IS updating a fact, not adding a complementary one). Recency happens to be right ~78% of the time because real users mostly restate / refine / update — they rarely create true complementary pairs about the same subject in ways the policy can't distinguish from contradictions.**

This was the predecessor project's exact failure mode. The OSS port has the same DNA and shows the same failure on every independent measurement.

## What this means for the claims in this repo

### Claims to RETRACT

- ~~"+40pp accuracy on LangMem"~~ — based on simulation; real pipeline shows **−5pp**
- ~~"+35-57pp polymorphism across 3 hosts"~~ — based on synthetic bench; real-data shows TypedMergePolicy LOSES to recency on all three
- ~~"resolver-oss solves conflicts in real systems"~~ — the policy does not generalize beyond synthetic; integration adds engineering value (the hook IS reusable) but the included policy is not a recommended default

### Claims that SURVIVE

- **`MergePolicy` Protocol as a community vocabulary.** The interface is reusable. Adapters work. The HOOK is load-bearing infrastructure.
- **`agent-merge-bench` as measurement infrastructure.** The harness, ECE, per-type breakdown, over/under-resolution metrics — all valid and useful. The 9 baselines remain reference implementations.
- **The 5-type taxonomy as a vocabulary.** Inter-labeller 0.929 type / 1.000 decision on a 14-scenario sample. The vocabulary is reproducible.
- **`PassthroughPolicy` and `RecencyPolicy` as references.** These work correctly and don't over-claim.
- **The methodology lessons are louder than ever:**
  - "Held-out before ship" — repeated case for it
  - "Adversarial baseline (recency) first" — recency beat the clever architecture on real data by 37pp
  - "Ablations over headlines" — the ablation `routed-contra-recency` already showed the LLM contradiction specialist adds 0pp on synthetic; real data confirms recency is the right policy

### What changes for v0.2

The original v0.2 roadmap (in `docs/v0.2-roadmap.md`) assumed TypedMergePolicy was the headline contribution and the work was about more adapters + more benches. Given this verdict, the correct v0.2 framing is:

1. **DEMOTE TypedMergePolicy** to a "reference baseline that demonstrates the API surface, with documented failure modes." It is not recommended for production.
2. **PROMOTE the Protocol + adapters + bench** as the actual contribution. The MERGE-POLICY-AS-PLUGIN hypothesis is correct; the SPECIFIC PROVIDED POLICY is not the right default.
3. **Build better policies.** Possible directions:
   - LLM-confidence-aware ESCALATE — only escalate when LLM classifier confidence ≤ X
   - Hybrid: recency by default, ESCALATE only on detected real complementary (different attributes, distinct evidence_refs)
   - Type-aware where the TYPE itself comes from substrate metadata, not LLM inference
4. **Real-data first.** Future architecture iteration must use the LongMemEval-derived bench (or similar) AS the dev set. The synthetic bench is now known to mislead.

## What the user-facing README says now

Updated. The headline is no longer "+40pp on LangMem." The headline is now:

> v0.1.0-alpha: the API + harness + adapter pattern is the contribution. The included `TypedMergePolicy` does not beat recency on real data; treated as a reference implementation with documented failure modes. See HONEST-VERDICT-2026-05-31.md.

## Process honesty: how this got caught

The earlier docs (`multi-adapter-leaderboard.md`, `langmem-integration-2026-05-31.md`) reported synthetic numbers as if they generalized. They specifically said "the +40pp accuracy delta is an upper bound on real production performance (held-out validation in the predecessor project crashed the same architecture to 0.650)" — but reading those docs without context, a casual reader would internalize "+40pp." That was a framing failure on the optimistic side.

The two measurements in this session that exposed it:
- Full 60-scenario e2e LangMem (cost ~$5; took ~12 minutes wall-clock)
- LongMemEval-S extraction + run (cost ~$5; took ~20 minutes)

Both were straightforward extensions. Neither required new ideas. They just required actually doing the measurement instead of stopping at simulation.

Lesson: **simulate to validate the wiring; measure to validate the claim.** The wiring (Protocol + adapters + monkey-patch) was validated by simulation. The claim ("TypedMergePolicy improves on stock") required real-pipeline + real-data measurement. We had both within reach the whole session; we should have prioritized them earlier.

## What ships honestly

- `MergePolicy` Protocol + dataclasses + validators: ✅ correct and useful
- `PassthroughPolicy`, `RecencyPolicy`: ✅ correct reference implementations
- `TypedMergePolicy`: ⚠️ ships with prominent caveat — does not beat recency on real data; included as reference for future architecture iteration
- 4 adapters (BenchAdapter, LangMemBenchAdapter, LangMemE2EAdapter, Mem0BenchAdapter, LettaBenchAdapter): ✅ adapter pattern works
- `scenarios/longmemeval-derived.json` (real-data bench): ✅ first iteration; methodology should be replicated for other sources
- Multi-adapter leaderboard table: needs revision to lead with the real-data result, not the synthetic

## Recommendation for the LangMem RFC

Hold. The RFC's §5 commitment to LongMemEval deltas was the right shape. The deltas are negative-or-null on the included policy. Posting now would either be misleading (if framed as the simulation results) or a withdrawal of contribution (if framed accurately as "we built the hook, the included policy doesn't work, please add the hook anyway").

The intermediate path: ship `MergePolicy` Protocol as a stand-alone proposal (no recommended policy attached); position as "this is the abstraction the community needs; reference implementations are out of scope for the RFC." That's a smaller ask and a more honest one.

## What survives as actually useful

In rough order of value to a future contributor:

1. **The methodology** — three convergent measurements demonstrating an architectural pattern's failure to generalize is research-grade content. Worth a blog post / paper draft.
2. **The harness** (agent-merge-bench) — per-type accuracy + ECE + over/under-resolution + 9 reference baselines + inter-labeller pattern. Reusable for any future merge-policy work.
3. **The Protocol + adapter pattern** — proven to work across 3 architecturally distinct hosts (LangMem, Mem0, Letta). The hook itself is the contribution.
4. **The real-data extraction script** (`scripts/extract_from_longmemeval.py`) — replicable for other public datasets. 30 LongMemEval rows → 27 labeled scenarios in ~$2 + 20min.
5. **The taxonomy note** — vocabulary remains valid even though the included policy doesn't realize the implied gains.

**What is NOT useful as-is:**
- TypedMergePolicy as a production default
- The simulation-derived headline numbers (without the real-data caveat next to them)
- The "+40pp delta" framing in any external communication

## Cost of this finding

Cumulative API spend across this session: ~$15-20. Cumulative engineering time (Claude+user): ~5 hours. Output: one honest negative result + three pieces of reusable infrastructure (Protocol, harness, real-data extraction pattern) + documented methodology lessons.

This is a successful outcome by the standards in `research_methodology.md`. It is not a successful outcome by the standards of "ship a library that solves the problem." Both framings are defensible; the first is the one this project consistently chose.
