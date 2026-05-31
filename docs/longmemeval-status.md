# LongMemEval-S integration — status & roadmap

**Status:** NOT yet implemented. Documented here as the v0.2 measurement priority.
**Why not v0.1:** out of scope for this session's budget; full integration is a separate measurement framework, ~$20-30 API spend per run, and would benefit from real-traction signal (Phase 1 gate) before committing engineering time.

## Why LongMemEval matters here

The current agent-merge-bench measures one specific decision point: "given two writes about the same subject, what should happen?" That's necessary but not sufficient for the headline question the LangMem RFC commits to in its §5:

> agent-merge-bench × LangMem-stock vs LangMem+resolver-oss on **end-to-end QA** (LongMemEval / LoCoMo).

The bench-only delta tells us the policy WOULD change the store contents. LongMemEval tells us whether those changed store contents change the downstream QA answer. Without the second number, the contribution to the user-facing system is unmeasured.

## What LongMemEval-S is

- Subset of LongMemEval (arXiv:2409.18819) — multi-session QA over long agent conversations
- 500 questions across 5 capability axes (single-session, multi-session, temporal reasoning, knowledge updates, abstention)
- The "knowledge updates" axis is the interesting one for typed-merge: it tests whether the memory system correctly handles facts that change over time
- LangMem reports ~63-71% on full LongMemEval; Zep reports ~98% on DMR (a precursor)

## What integration would look like

```
For each LongMemEval question:
  1. Build a fresh MemoryStoreManager with InMemoryStore
  2. Feed the multi-session conversation history through ainvoke
  3. Ask the question via the memory system
  4. Score: did the answer match ground truth?

Compare:
  - Mode A: stock LangMem (default merge behavior)
  - Mode B: LangMem with TypedMergePolicy monkey-patched into _apply_manager_output
```

Same monkey-patch approach as `LangMemE2EAdapter`, but the measurement target is QA accuracy, not bench scenario accuracy.

## Cost estimate

- ~500 questions × ~10-20 LLM calls per question (extraction + retrieval + answering across multiple turns) = ~7,500-10,000 LLM calls
- Sonnet pricing: ~$50-100 per full run
- Two modes (stock + typed) = $100-200 per full evaluation

## What we'd learn

**Best case:** typed-merge adds 5-15pp on the knowledge-updates axis (where conflict resolution matters most). That's the case for the LangMem RFC.

**Mid case:** typed-merge is neutral on QA — the store contents change but downstream answering doesn't differ much. This would mean the contribution is "improves audit trail and ECE" but not user-facing.

**Worst case:** typed-merge HURTS QA in some scenarios because ESCALATE actions break the "single fact lookup" assumption. This would be the most important honest finding — would necessarily reshape the RFC.

## When this becomes a priority

This work is gated by traction. The audit's Phase 1 plan said: ship community artifacts (taxonomy + bench + RFC); IF a maintainer engages or external contributors materialize, THEN invest in deeper validation including LongMemEval. The full bench × adapter cross-product (this session) is enough to make the case in an RFC. LongMemEval is what closes the deal in a PR.

Specifically:
- (a) LangMem maintainer comments on the RFC saying "show us LongMemEval delta" → priority 1, start immediately
- (b) An external contributor opens an issue requesting LongMemEval comparison → priority 2
- (c) agent-merge-bench gets >100 stars / >5 contributors → priority 3 (signals community interest)

Without any of (a)-(c), LongMemEval is overinvestment.

## What we DO have without LongMemEval

The multi-adapter leaderboard (`docs/multi-adapter-leaderboard.md`) gives three independent measurements:
- LangMem +40pp accuracy
- Mem0 +35pp accuracy
- Letta +57pp accuracy

This is strong directional evidence that the policy moves the store-contents needle. The remaining open question is whether the changed contents change downstream QA — LongMemEval is the one that answers that.

## Predecessor project's analogous measurement

The resolver-internal predecessor project ran its routed architecture against a Nexos-internal "real data held-out" bench (the equivalent of LongMemEval for their use case). Result: dev 1.000 → held-out 0.650. That's the cautionary tale built into every public artifact here: bench numbers extrapolate poorly to real-data measurements.

LongMemEval-S, when run, may show a similar drop. The +35-57pp deltas above are an UPPER BOUND on what the policy can plausibly deliver on real QA. Honest expected range: +5-25pp at best, possibly 0 or negative on some axes.

## Open question on methodology

If the typed-merge POLICY itself sometimes returns ESCALATE on cases where a winner DOES exist (under-resolution), the host's QA may fail to find the right answer (no single canonical fact in the store). LangMem's retrieval would return both ESCALATEd memories + the supervisor would have to pick. That's not necessarily WORSE than recency (which would have committed the wrong fact entirely) but it's a different failure mode that QA-accuracy benchmarks measure directly.

Resolution: when LongMemEval integration ships, report per-capability-axis accuracy + per-axis under/over-resolution + ECE. Same three-number disclosure pattern as agent-merge-bench.
