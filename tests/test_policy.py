"""Tests for the public Protocol + Passthrough/Recency reference impls.

LLM-dependent TypedMergePolicy tests live in a separate file (require
ANTHROPIC_API_KEY); these tests are deterministic and run without the network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from resolver_oss import (
    Action,
    MemoryWrite,
    MergeDecision,
    MergePolicy,
    PassthroughPolicy,
    RecencyPolicy,
)


def _w(id_: str | None, text: str, ts_offset_min: int = 0) -> MemoryWrite:
    return MemoryWrite(
        id=id_,
        kind="Memory",
        content=text,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=ts_offset_min),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=ts_offset_min),
    )


def test_protocol_check_passthrough():
    assert isinstance(PassthroughPolicy(), MergePolicy)


def test_protocol_check_recency():
    assert isinstance(RecencyPolicy(), MergePolicy)


def test_passthrough_no_existing_adds():
    d = PassthroughPolicy().handle(_w(None, "fresh fact"), [])
    assert d.action == "ADD"
    assert d.target_id is None


def test_passthrough_id_collision_updates():
    existing = _w("mem-1", "old text")
    incoming = _w("mem-1", "new text")
    d = PassthroughPolicy().handle(incoming, [existing])
    assert d.action == "UPDATE"
    assert d.target_id == "mem-1"


def test_passthrough_no_id_collision_adds():
    existing = _w("mem-1", "old")
    incoming = _w("mem-2", "different")
    d = PassthroughPolicy().handle(incoming, [existing])
    assert d.action == "ADD"


def test_recency_no_existing_adds():
    d = RecencyPolicy().handle(_w(None, "first"), [])
    assert d.action == "ADD"


def test_recency_with_existing_supersedes_latest():
    old = _w("mem-old", "old fact", ts_offset_min=0)
    older = _w("mem-older", "older fact", ts_offset_min=-30)
    incoming = _w("mem-new", "newest", ts_offset_min=10)
    d = RecencyPolicy().handle(incoming, [old, older])
    assert d.action == "SUPERSEDE_BY_TIME"
    assert d.target_id == "mem-old"  # most recent of the existing


def test_memory_write_text_handles_str_content():
    w = MemoryWrite(id="x", kind="Memory", content="plain string fact")
    assert w.text == "plain string fact"


def test_memory_write_text_handles_dict_content():
    w = MemoryWrite(id="x", kind="Memory", content={"content": "nested fact"})
    assert w.text == "nested fact"


def test_merge_decision_defaults():
    d = MergeDecision(action="ADD")
    assert d.target_id is None
    assert d.confidence == 1.0
    assert d.rationale == ""
    assert d.metadata == {}


def test_action_type_enum_values_construct_with_required_fields():
    # Per validator: ADD/ESCALATE → no target_id; UPDATE/DEDUPE/SUPERSEDE → target_id required
    MergeDecision(action="ADD")
    MergeDecision(action="ESCALATE")
    MergeDecision(action="UPDATE", target_id="x")
    MergeDecision(action="DEDUPE", target_id="x")
    MergeDecision(action="SUPERSEDE_BY_TIME", target_id="x")


def test_merge_decision_rejects_missing_target_id():
    import pytest
    for action in ("UPDATE", "DEDUPE", "SUPERSEDE_BY_TIME"):
        with pytest.raises(ValueError, match="requires target_id"):
            MergeDecision(action=action)  # type: ignore[arg-type]


def test_merge_decision_rejects_target_id_on_add():
    import pytest
    with pytest.raises(ValueError, match="must not carry target_id"):
        MergeDecision(action="ADD", target_id="x")


def test_merge_decision_rejects_out_of_range_confidence():
    import pytest
    with pytest.raises(ValueError, match="confidence"):
        MergeDecision(action="ADD", confidence=1.5)


def test_memory_write_rejects_out_of_range_confidence():
    import pytest
    with pytest.raises(ValueError, match="confidence"):
        MemoryWrite(id=None, kind="Memory", content="x", confidence=2.0)


def test_policy_error_is_exported():
    from resolver_oss import PolicyError
    assert issubclass(PolicyError, Exception)
