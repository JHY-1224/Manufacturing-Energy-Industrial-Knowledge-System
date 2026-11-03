from __future__ import annotations

import math

from app.core.models import RagChunk
from app.indexes.metadata_index import MetadataIndex


class ChromaStore:
    """In-memory Chroma-compatible adapter used by the local demo."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[RagChunk, list[float]]] = {}

    def upsert(self, chunk: RagChunk, vector: list[float]) -> None:
        self.rows[chunk.chunk_id] = (chunk, vector)

    def search(self, query_vector: list[float], top_k: int = 10, metadata_filter: dict | None = None):
        results: list[tuple[RagChunk, float]] = []
        for chunk, vector in self.rows.values():
            if metadata_filter and not MetadataIndex.match(chunk.metadata, metadata_filter):
                continue
            score = sum(left * right for left, right in zip(query_vector, vector))
            score = (score + 1.0) / 2.0
            results.append((chunk, max(0.0, min(1.0, score))))
        return sorted(results, key=lambda item: item[1], reverse=True)[:top_k]

    def count(self) -> int:
        return len(self.rows)
