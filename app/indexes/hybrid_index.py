from app.core.models import RetrievalHit
from concurrent.futures import ThreadPoolExecutor


class HybridIndex:
    def __init__(self, vector_index, bm25_index) -> None:
        self.vector_index = vector_index
        self.bm25_index = bm25_index

    def search(self, query: str, top_k: int, vector_weight: float = 0.7, bm25_weight: float = 0.3, metadata_filter: dict | None = None, fusion_method: str = "weighted") -> list[RetrievalHit]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='hybrid-recall') as pool:
            vector_result = pool.submit(self.vector_index.search, query, top_k * 2, metadata_filter)
            bm25_result = pool.submit(self.bm25_index.search, query, top_k * 2, metadata_filter)
            vector_hits, bm25_hits = vector_result.result(), bm25_result.result()
        merged: dict[str, RetrievalHit] = {}
        for rank, hit in enumerate(vector_hits, 1):
            hit.vector_rank = rank
            hit.retrieval_sources = ["vector"]
            hit.score = 1 / (60 + rank) if fusion_method == "rrf" else hit.vector_score * vector_weight
            merged[hit.chunk_id] = hit
        for rank, hit in enumerate(bm25_hits, 1):
            if hit.bm25_score <= 0:
                continue
            score = 1 / (60 + rank) if fusion_method == "rrf" else hit.bm25_score * bm25_weight
            if hit.chunk_id in merged:
                merged[hit.chunk_id].bm25_score = hit.bm25_score
                merged[hit.chunk_id].bm25_rank = rank
                merged[hit.chunk_id].retrieval_sources.append("bm25")
                merged[hit.chunk_id].score += score
            else:
                hit.score, hit.bm25_rank, hit.retrieval_sources = score, rank, ["bm25"]
                merged[hit.chunk_id] = hit
        for hit in merged.values():
            hit.fusion_score = hit.score
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:top_k]
