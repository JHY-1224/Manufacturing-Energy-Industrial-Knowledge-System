from __future__ import annotations

import math
from collections import Counter

from app.core.models import RagChunk, RetrievalHit
from app.embeddings.embedding_factory import tokenize
from app.indexes.metadata_index import MetadataIndex


class BM25Index:
    # Index user-facing descriptive fields; hashes, local paths, policies and OCR
    # coordinates are provenance, not terms. They previously diluted short captions.
    SEARCHABLE_FIELDS=('source','section','title','domain','task','doc_type','chunk_type',
        'assigned_category','source_caption','visible_topic_or_use','material','equipment',
        'variable_name','unit','keywords','tags','fault_type')
    def __init__(self, tokenizer=tokenize, searchable_fields=None) -> None:
        self.tokenize = tokenizer
        self.searchable_fields = self.SEARCHABLE_FIELDS if searchable_fields is None else searchable_fields
        self.chunks: list[RagChunk] = []
        self.term_frequencies: list[Counter[str]] = []
        self.document_frequency: Counter[str] = Counter()
        self.average_length = 1.0

    def build(self, chunks: list[RagChunk]) -> None:
        self.chunks = chunks
        self.term_frequencies = [Counter(self.tokenize(chunk.content + " " + " ".join(str(chunk.metadata[k]) for k in self.searchable_fields if k in chunk.metadata))) for chunk in chunks]
        self.document_frequency = Counter()
        for terms in self.term_frequencies:
            self.document_frequency.update(terms.keys())
        self.average_length = sum(sum(terms.values()) for terms in self.term_frequencies) / max(1, len(chunks))

    def search(self, query: str, top_k: int, metadata_filter: dict | None = None) -> list[RetrievalHit]:
        query_terms = self.tokenize(query)
        scores: list[tuple[RagChunk, float]] = []
        total_docs = max(1, len(self.chunks))
        for chunk, frequencies in zip(self.chunks, self.term_frequencies):
            if metadata_filter and not MetadataIndex.match(chunk.metadata, metadata_filter):
                continue
            length = sum(frequencies.values()) or 1
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self.document_frequency.get(term, 0)
                inverse = math.log(1 + (total_docs - document_frequency + 0.5) / (document_frequency + 0.5))
                score += inverse * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * length / self.average_length))
            scores.append((chunk, score))
        maximum = max((score for _, score in scores), default=1.0) or 1.0
        ranked = sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]
        return [RetrievalHit(doc_id=chunk.doc_id, chunk_id=chunk.chunk_id, content=chunk.content, metadata=chunk.metadata, score=score / maximum, bm25_score=score / maximum, bm25_rank=rank, retrieval_sources=['bm25']) for rank,(chunk, score) in enumerate(ranked,1) if score > 0]
