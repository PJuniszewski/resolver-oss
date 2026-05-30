"""resolver-oss — typed semantic conflict resolution for multi-agent memory.

Pluggable merge-policy library. Drop into Mem0, Zep, LangMem, Letta, Cognee,
or any other memory stack at the pre-commit hook. Reports per-decision
confidence for ECE calibration tracking.

Public API:
    MergePolicy        — Protocol; any object with .handle(incoming, existing)
    MemoryWrite        — input dataclass (one write candidate or existing memory)
    MergeDecision      — output dataclass (action + target_id + rationale + confidence)
    Action             — Literal enum of 5 actions

Reference implementations:
    TypedMergePolicy   — LLM classifier + refinement specialist + deterministic
                         escalation. Default model: claude-sonnet-4-6. Real API
                         calls (no stubs). ~2 LLM calls per resolve worst case.
    PassthroughPolicy  — Returns whatever the upstream system would have done
                         (last-write-wins on id collision, ADD otherwise).
                         Use as adapter default to preserve current behavior.
    RecencyPolicy      — Newest write wins. Baseline for comparison.

Adapters (separate optional installs):
    See `resolver_oss.adapters.langmem` (extras: `pip install resolver-oss[langmem]`)
"""
from .policy import (
    Action,
    MemoryWrite,
    MergeDecision,
    MergePolicy,
    PassthroughPolicy,
    PolicyError,
    RecencyPolicy,
    TypedMergePolicy,
)

__all__ = [
    "Action",
    "MemoryWrite",
    "MergeDecision",
    "MergePolicy",
    "PassthroughPolicy",
    "PolicyError",
    "RecencyPolicy",
    "TypedMergePolicy",
]

__version__ = "0.1.0-alpha"
