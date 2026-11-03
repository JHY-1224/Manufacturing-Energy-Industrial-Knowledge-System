from app.core.models import RagChunk, RetrievalHit


class VectorIndex:
    def __init__(self, embedding, store) -> None:
        self.embedding = embedding
        self.store = store

    def build(self, chunks: list[RagChunk]) -> None:
        for chunk in chunks:
            self.store.upsert(chunk, self.embedding.embed(chunk.content))

    def search(self, query: str, top_k: int, metadata_filter: dict | None = None) -> list[RetrievalHit]:
        rows = self.store.search(self.embedding.embed(query), top_k, metadata_filter)
        return [RetrievalHit(doc_id=chunk.doc_id, chunk_id=chunk.chunk_id, content=chunk.content, metadata=chunk.metadata, score=score, vector_score=score, vector_rank=rank, retrieval_sources=['vector']) for rank,(chunk, score) in enumerate(rows,1)]
