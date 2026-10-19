"""Hybrid semantic and BM25 retrieval with company/year filters and stable citations."""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import settings

CHUNKS = Path("data/chunks/chunks.jsonl")
COLLECTION = "sec_filings"
_model = None
_bm25 = None
_rows = None


def _load(*, with_model: bool = True):
    global _model, _bm25, _rows
    if _rows is None:
        from rank_bm25 import BM25Okapi

        _rows = [json.loads(line) for line in CHUNKS.read_text(encoding="utf-8").splitlines() if line.strip()]
        _bm25 = BM25Okapi([re.findall(r"[a-z0-9]+", str(row["text"]).lower()) for row in _rows])
    if with_model and _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding_model)
    return _rows, _bm25, _model


def retrieve(query: str, company: str | None = None, year: str | None = None, k: int = 5, mode: str = "hybrid") -> list[dict]:
    if mode not in {"hybrid", "dense", "bm25"}:
        raise ValueError("mode must be one of: hybrid, dense, bm25")
    rows, bm25, model = _load(with_model=mode != "bm25")

    filters = []
    if company:
        filters.append({"company": company.upper()})
    if year:
        filters.append({"fiscal_year": str(year)})
    where = {"$and": filters} if len(filters) > 1 else (filters[0] if filters else None)
    semantic_rank = {}
    if mode != "bm25":
        import chromadb

        client = chromadb.PersistentClient(path=settings.chroma_path)
        collection = client.get_collection(COLLECTION)
        vec = model.encode(query, normalize_embeddings=True).tolist()
        semantic = collection.query(query_embeddings=[vec], n_results=min(max(k * 4, 20), len(rows)), where=where)
        semantic_rank = {doc_id: i for i, doc_id in enumerate(semantic["ids"][0])}
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    lexical_scores = bm25.get_scores(tokens)
    filtered = [r for r in rows if (not company or r["company"] == company.upper()) and (not year or str(r["fiscal_year"]) == str(year))]
    score_by_id = {row["id"]: lexical_scores[i] for i, row in enumerate(rows)}
    lexical_order = sorted(filtered, key=lambda r: score_by_id[r["id"]], reverse=True)[: max(k * 4, 20)]
    lexical_rank = {r["id"]: i for i, r in enumerate(lexical_order)}
    by_id = {r["id"]: r for r in rows}
    if mode == "dense":
        ranked = sorted(semantic_rank, key=semantic_rank.get)
    elif mode == "bm25":
        ranked = [r["id"] for r in lexical_order]
    else:
        candidates = set(semantic_rank) | set(lexical_rank)
        ranked = sorted(candidates, key=lambda x: 0.5 / (60 + semantic_rank.get(x, 10000)) + 0.5 / (60 + lexical_rank.get(x, 10000)), reverse=True)
    return [by_id[doc_id] for doc_id in ranked[:k] if doc_id in by_id]


def retrieve_with_ranks(
    query: str, company: str | None = None, year: str | None = None, k: int = 5,
    mode: str = "hybrid",
) -> list[dict]:
    """Retrieve passages and expose the three rank positions for transparent inspection."""
    if mode not in {"hybrid", "dense", "bm25"}:
        raise ValueError("mode must be one of: hybrid, dense, bm25")
    rows, bm25, model = _load(with_model=mode != "bm25")

    filters = []
    if company:
        filters.append({"company": company.upper()})
    if year:
        filters.append({"fiscal_year": str(year)})
    where = {"$and": filters} if len(filters) > 1 else (filters[0] if filters else None)
    count = min(max(k * 4, 20), len(rows))
    dense_rank = {}
    if mode != "bm25":
        import chromadb

        collection = chromadb.PersistentClient(path=settings.chroma_path).get_collection(COLLECTION)
        dense_result = collection.query(
            query_embeddings=[model.encode(query, normalize_embeddings=True).tolist()],
            n_results=count,
            where=where,
        )
        dense_rank = {doc_id: i + 1 for i, doc_id in enumerate(dense_result["ids"][0])}
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    scores = bm25.get_scores(tokens)
    filtered = [
        (i, row) for i, row in enumerate(rows)
        if (not company or row["company"] == company.upper())
        and (not year or str(row["fiscal_year"]) == str(year))
    ]
    lexical_order = [row for _, row in sorted(filtered, key=lambda pair: scores[pair[0]], reverse=True)[:count]]
    lexical_rank = {row["id"]: i + 1 for i, row in enumerate(lexical_order)}
    candidates = set(dense_rank) | set(lexical_rank)
    fused_order = sorted(
        candidates,
        key=lambda doc_id: 0.5 / (60 + dense_rank.get(doc_id, 10000))
        + 0.5 / (60 + lexical_rank.get(doc_id, 10000)),
        reverse=True,
    )
    fused_rank = {doc_id: i + 1 for i, doc_id in enumerate(fused_order)}
    chosen = {"dense": dense_rank, "bm25": lexical_rank, "hybrid": fused_rank}[mode]
    by_id = {row["id"]: row for row in rows}
    selected = sorted(chosen, key=chosen.get)[:k]
    return [
        {
            **by_id[doc_id],
            "ranks": {
                "bm25": lexical_rank.get(doc_id),
                "dense": dense_rank.get(doc_id),
                "rrf": fused_rank.get(doc_id),
            },
        }
        for doc_id in selected if doc_id in by_id
    ]
