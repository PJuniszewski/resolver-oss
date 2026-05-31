"""Extract conflict pairs from LongMemEval-S → agent-merge-bench scenarios format.

Approach:
1. Load N rows from kellyhongg/cleaned-longmemeval-s (HuggingFace)
2. For each row, parse focused_input into session-attributed user turns
3. Use sonnet-4-6 to extract pairs of claims about the same subject where one
   could supersede/refine/duplicate the other
4. LLM also classifies the 5-type conflict + assigns ground_truth
5. Save as scenarios JSON in agent-merge-bench schema

This is REAL (public) data, not synthetic. It addresses the "production traffic
delta" gap in the v0.2 roadmap.

Run:
    python scripts/extract_from_longmemeval.py --n 30 --out scenarios/longmemeval-derived.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Resolve imports — script lives one level above package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EXTRACTION_PROMPT = """\
You extract pairs of conflicting facts from a multi-session conversation.

A "conflict pair" is two user statements about the SAME subject where:
- CONTRADICTION: same attribute, mutually exclusive values (e.g., "I work at A" vs "I work at B")
- REFINEMENT: one is strictly more specific (e.g., "meeting Tuesday" vs "meeting Tuesday 3PM PT")
- COMPLEMENTARY: different facets of same entity (e.g., "My title is PM" vs "I live in Berlin")
- TEMPORAL_SCOPE: same attribute, different time scope (e.g., "lived in NYC 2018-2020" vs "live in SF since 2020")
- DUPLICATE: same fact, restated (e.g., "Meeting Tuesday" vs "Tuesdays standup")

Read the conversation and extract UP TO 2 high-quality conflict pairs. Skip if no clear conflict exists.
For each pair, identify:
- claim_1: first statement (exact quote or close paraphrase)
- claim_1_session: session date/time
- claim_2: second statement (exact quote or close paraphrase)
- claim_2_session: session date/time
- conflict_type: one of the 5 above
- decision_kind: "winner" / "merge" / "escalate" — what should resolved memory be?
- winner_idx: 0 or 1 (claim_1=0, claim_2=1) — required for winner/merge
- rationale: 1 sentence why

CONVERSATION:
{conversation}

Respond with ONE JSON object only:
{{
  "pairs": [
    {{"claim_1": "...", "claim_1_session": "...", "claim_2": "...", "claim_2_session": "...",
      "conflict_type": "contradiction|refinement|complementary|temporal_scope|duplicate",
      "decision_kind": "winner|merge|escalate",
      "winner_idx": 0|1|null,
      "rationale": "..."}}
  ]
}}

If no clear conflicts found, return {{"pairs": []}}.
"""


def parse_focused_input(focused_input: str) -> list[dict]:
    """Extract user turns with session attribution from LongMemEval focused_input."""
    turns = []
    # Pattern: "Session YYYY/MM/DD (Day) HH:MM:" followed by message dict
    session_re = re.compile(r"Session ([\d/]+)\s*\(([A-Za-z]+)\)\s*([\d:]+):")

    sessions = re.split(session_re, focused_input)
    # sessions[0] is preamble; then groups of [date, day, time, content] × N
    for i in range(1, len(sessions), 4):
        if i + 3 >= len(sessions):
            break
        date, day, time, content = sessions[i:i+4]
        # Extract user messages
        for m in re.finditer(r"\{'role':\s*'user',\s*'content':\s*\"([^\"]+)\"\}", content):
            turns.append({
                "session": f"{date} {time}",
                "text": m.group(1),
            })
    return turns


def conversation_to_prompt_text(turns: list[dict], max_turns: int = 12) -> str:
    """Format turns for the extraction LLM prompt."""
    if len(turns) > max_turns:
        # Keep first 2 + last (max_turns-2) so we get original + latest context
        turns = turns[:2] + turns[-(max_turns - 2):]
    return "\n\n".join(f"[{t['session']}] User: {t['text']}" for t in turns)


def extract_pairs_from_row(row: dict, model: str, api_key: str) -> list[dict]:
    """LLM-extract conflict pairs from one LongMemEval row."""
    from resolver_oss._llm import call_with_cache, parse_json_response

    turns = parse_focused_input(row.get("focused_input", ""))
    if len(turns) < 2:
        return []

    convo = conversation_to_prompt_text(turns)
    prompt = EXTRACTION_PROMPT.format(conversation=convo)

    # Use a separate cache tag so this doesn't collide with policy classifier cache
    raw = call_with_cache(model, f"longmemeval-extract-{row.get('custom_id', 'x')}", prompt, api_key=api_key)
    try:
        payload = parse_json_response(raw)
        pairs = payload.get("pairs", [])
        if not isinstance(pairs, list):
            return []
        return pairs
    except Exception as e:
        print(f"  parse error on row {row.get('custom_id')}: {e}", file=sys.stderr)
        return []


def pair_to_scenario(pair: dict, source_id: str, idx: int) -> dict | None:
    """Convert one extracted pair into an agent-merge-bench scenario dict."""
    if not pair.get("claim_1") or not pair.get("claim_2"):
        return None

    ctype = (pair.get("conflict_type") or "").lower().strip()
    if ctype not in {"contradiction", "refinement", "complementary", "temporal_scope", "duplicate"}:
        return None

    dk = (pair.get("decision_kind") or "").lower().strip()
    if dk not in {"winner", "merge", "escalate"}:
        return None

    widx = pair.get("winner_idx")
    if dk in {"winner", "merge"}:
        if not isinstance(widx, int) or widx not in (0, 1):
            # Default to idx 1 (later claim) for winner/merge if missing
            widx = 1
    else:
        widx = None

    # Build agent-merge-bench schema-compatible scenario
    return {
        "id": f"lme-{source_id[:6]}-{idx:02d}",
        "description": pair.get("rationale", "")[:200],
        "conflict_type": ctype,
        "writes": [
            {
                "author": "longmemeval-user",
                "claim": pair["claim_1"].strip()[:500],
                "timestamp": _normalize_session_ts(pair.get("claim_1_session", "2023-01-01 00:00")),
                "evidence_kind": "user_statement",
                "evidence_ref": f"longmemeval-{source_id}-s1",
                "confidence": 0.85,
            },
            {
                "author": "longmemeval-user",
                "claim": pair["claim_2"].strip()[:500],
                "timestamp": _normalize_session_ts(pair.get("claim_2_session", "2023-01-02 00:00")),
                "evidence_kind": "user_statement",
                "evidence_ref": f"longmemeval-{source_id}-s2",
                "confidence": 0.85,
            },
        ],
        "ground_truth": {
            "decision_kind": dk,
            "winner_idx": widx,
            "merge_summary": pair["claim_2"] if dk == "merge" else None,
            "rationale": pair.get("rationale", "")[:300],
        },
        "should_escalate": dk == "escalate",
        "notes": f"Extracted from LongMemEval-S row {source_id} via sonnet-4-6 LLM extraction (see scripts/extract_from_longmemeval.py)",
    }


def _normalize_session_ts(s: str) -> str:
    """Try to parse the session date string into an ISO timestamp."""
    # LongMemEval sessions look like "2023/02/15 06:30" or similar
    s = s.strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M:00Z")
        except ValueError:
            continue
    # Fallback: try to pull out YYYY/MM/DD
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T00:00:00Z"
    return "2023-01-01T00:00:00Z"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=30, help="Number of LongMemEval rows to process")
    p.add_argument("--out", type=Path, default=Path("scenarios/longmemeval-derived.json"))
    p.add_argument("--model", default="claude-sonnet-4-6")
    args = p.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY required for extraction", file=sys.stderr)
        return 1

    from datasets import load_dataset
    print(f"Loading {args.n} rows from kellyhongg/cleaned-longmemeval-s...", file=sys.stderr)
    ds = load_dataset("kellyhongg/cleaned-longmemeval-s", split=f"train[:{args.n}]")

    scenarios: list[dict] = []
    skipped = 0
    for i, row in enumerate(ds):
        cid = row.get("custom_id", f"row{i}")
        try:
            pairs = extract_pairs_from_row(row, args.model, api_key)
        except Exception as e:
            print(f"  [{i+1}/{args.n}] {cid}: extract error {e}", file=sys.stderr)
            skipped += 1
            continue
        if not pairs:
            skipped += 1
            print(f"  [{i+1}/{args.n}] {cid}: no pairs extracted", file=sys.stderr)
            continue
        for j, pair in enumerate(pairs):
            sc = pair_to_scenario(pair, cid, j)
            if sc:
                scenarios.append(sc)
        print(f"  [{i+1}/{args.n}] {cid}: +{len(pairs)} pair(s) (total scenarios: {len(scenarios)})", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    bench = {"scenarios": scenarios}
    args.out.write_text(json.dumps(bench, indent=2) + "\n")
    print(f"\nWrote {len(scenarios)} scenarios to {args.out}", file=sys.stderr)
    print(f"Source rows processed: {args.n}, skipped: {skipped}", file=sys.stderr)

    # Distribution by type
    from collections import Counter
    types = Counter(s["conflict_type"] for s in scenarios)
    print("Distribution by conflict_type:", file=sys.stderr)
    for t, c in types.most_common():
        print(f"  {t}: {c}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
