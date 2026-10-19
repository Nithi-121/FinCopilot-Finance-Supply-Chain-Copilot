"""Evaluate retrieval against human-verified gold chunk IDs and track basic citation safety."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from src.retrieve import retrieve


def run(path: Path, k: int = 5, mode: str = "hybrid") -> dict:
    questions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    answerable = [q for q in questions if q.get("answerable", True)]
    if not answerable:
        raise ValueError("Evaluation set has no answerable questions")
    outcomes = []
    for item in answerable:
        results = retrieve(item["question"], item.get("company"), item.get("year"), k, mode=mode)
        gold_ids = set(item.get("gold_chunk_ids", []))
        gold_sections = {s.lower() for s in item.get("gold_sections", [])}
        if gold_ids:
            relevant = [rank for rank, result in enumerate(results, 1) if result["id"] in gold_ids]
        else:
            relevant = [rank for rank, result in enumerate(results, 1) if result["section"].lower() in gold_sections]
        outcomes.append({
            "company": item.get("company") or "unspecified",
            "sections": sorted(gold_sections) or ["unspecified"],
            "hit_at_k": bool(relevant),
            "reciprocal_rank": 1 / relevant[0] if relevant else 0.0,
        })

    def grouped(group_key: str) -> dict[str, dict[str, float | int]]:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for result in outcomes:
            labels = result[group_key] if group_key == "sections" else [result[group_key]]
            for label in labels:
                buckets[label].append(result)
        return {
            label: {
                "questions": len(rows),
                "hit_at_k": sum(row["hit_at_k"] for row in rows) / len(rows),
                "mrr_at_k": sum(row["reciprocal_rank"] for row in rows) / len(rows),
            }
            for label, rows in sorted(buckets.items())
        }

    has_chunk_ids = [bool(q.get("gold_chunk_ids")) for q in answerable]
    gold_level = "chunk_id" if all(has_chunk_ids) else "section" if not any(has_chunk_ids) else "mixed"
    summary = {
        "questions": len(questions),
        "answerable": len(answerable),
        "unanswerable": len(questions) - len(answerable),
        "hit_at_k": sum(row["hit_at_k"] for row in outcomes) / len(outcomes),
        "mrr_at_k": sum(row["reciprocal_rank"] for row in outcomes) / len(outcomes),
        "k": k,
        "retrieval_mode": mode,
        "gold_level": gold_level,
        "by_company": grouped("company"),
        "by_section": grouped("sections"),
        "unanswerable_evaluation": "not_run; requires generated answers and refusal review",
    }
    return summary


def run_ablation(path: Path, k: int = 5) -> dict:
    return {mode: run(path, k, mode) for mode in ("bm25", "dense", "hybrid")}


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval/questions.jsonl")
    results = run_ablation(source) if "--ablation" in sys.argv else run(source)
    print(json.dumps(results, indent=2))
