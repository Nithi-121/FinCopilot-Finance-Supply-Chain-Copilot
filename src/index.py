"""Persist searchable chunks and local sentence-transformer embeddings in Chroma."""
import json
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from src.config import settings

CHUNKS = Path("data/chunks/chunks.jsonl")
COLLECTION = "sec_filings"


def build_index() -> int:
    if not CHUNKS.exists():
        raise FileNotFoundError("Run `python -m src.parse` before indexing.")
    rows = [json.loads(line) for line in CHUNKS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("Chunk file is empty")
    model = SentenceTransformer(settings.embedding_model)
    client = chromadb.PersistentClient(path=settings.chroma_path)
    collection = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    # Chroma upserts are idempotent; batches keep memory bounded.
    for start in range(0, len(rows), 128):
        batch = rows[start : start + 128]
        embeddings = model.encode([str(r["text"]) for r in batch], normalize_embeddings=True).tolist()
        collection.upsert(
            ids=[str(r["id"]) for r in batch],
            documents=[str(r["text"]) for r in batch],
            metadatas=[{k: str(v) for k, v in r.items() if k not in {"id", "text"}} for r in batch],
            embeddings=embeddings,
        )
    return len(rows)


if __name__ == "__main__":
    print(f"Indexed {build_index()} chunks")
