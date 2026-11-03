from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class RagChunk(BaseModel):
    doc_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    parent_content: str | None = None


class QueryOptions(BaseModel):
    runtime_mode: Literal["offline", "configured"] = "offline"
    embedding: str = "bge-large-zh"
    vectorstore: str = "chroma"
    retrieval_mode: Literal["vector", "bm25", "hybrid", "parent_child", "parent_child_hybrid", "summary", "rag_fusion"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=100)
    final_top_k: int = Field(default=5, ge=1, le=100)
    vector_weight: float = Field(default=0.7, ge=0, le=1)
    bm25_weight: float = Field(default=0.3, ge=0, le=1)
    fusion_method: Literal["weighted", "rrf"] = "weighted"
    query_rewrite: bool = True
    query_expansion: bool = True
    query_transformation: bool = True
    metadata_router: bool = True
    multi_query: bool = False
    hyde: bool = False
    rerank: bool = True
    reranker_model: str = "score-fallback"
    compression: bool = True
    compression_mode: str = "auto"
    deduplicate: bool = True
    document_filter: bool = True
    parent_recovery: bool = True
    image_context: bool = True
    review_mode: Literal["reference", "published_only"] = "reference"
    table_retrieval: bool = False
    cross_language_query: bool = False
    evidence_retrieval: bool = False
    rag_fusion: bool = False
    metadata_filter: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    options: QueryOptions = Field(default_factory=QueryOptions)
    conversation_context: str = ""
    generate_answer: bool = True


class RetrievalHit(BaseModel):
    doc_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rerank_score: float | None = None
    original_rank: int | None = None
    final_rank: int | None = None
    retrieval_sources: list[str] = Field(default_factory=list)
    vector_rank: int | None = None
    bm25_rank: int | None = None
    fusion_score: float | None = None


class QueryTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: f"trace_{uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    original_query: str
    rewritten_query: str
    expanded_queries: list[str] = Field(default_factory=list)
    metadata_filter: dict[str, Any] = Field(default_factory=dict)
    retrieval_mode: str
    options: dict[str, Any] = Field(default_factory=dict)
    retrieved_docs: list[RetrievalHit] = Field(default_factory=list)
    reranked_docs: list[RetrievalHit] = Field(default_factory=list)
    compressed_context: list[str] = Field(default_factory=list)
    answer: str = ""
    latency_ms: dict[str, float] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost: dict[str, float | None] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    corpus_version: int = 0


class EvalCase(BaseModel):
    query_id: str
    query: str
    category: str
    gold_doc_ids: list[str] = Field(default_factory=list)
    ground_truth: str | None = None
    relevance: dict[str, float] = Field(default_factory=dict)


class EvalRunRequest(BaseModel):
    eval_set: str = "data/eval_sets/energy_rag_eval.jsonl"
    experiment_name: str = "default_energy_rag"
    options: QueryOptions = Field(default_factory=QueryOptions)


class IngestRequest(BaseModel):
    expected_write_version: str | None = None
    path: str
    domain: str = "电气工程基础"
    doc_type: str = "技术资料"
    splitter: str = "recursive"
    chunk_size: int = Field(default=700, ge=200, le=16000)
    chunk_overlap: int = Field(default=120, ge=0, le=8000)
    document_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parser_mode: Literal["native", "auto", "ocr"] = "native"
    knowledge_base_id: str | None = None


class EnergyEntity(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    domain: str
    description: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnergyRelation(BaseModel):
    relation_id: str
    source_entity: str
    target_entity: str
    relation_type: str
    domain: str
    description: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphRetrievalOptions(BaseModel):
    mode: str = "hybrid_graph"
    domain: str | None = None
    max_hops: int = 2
    top_k_entities: int = 10
    top_k_relations: int = 10
    include_chunk_context: bool = True
    include_graph_context: bool = True
    knowledge_base_id: str | None = None
    review_mode: Literal["reference", "published_only"] = "reference"


class GraphRetrievalRequest(BaseModel):
    question: str
    options: GraphRetrievalOptions = Field(default_factory=GraphRetrievalOptions)


class GraphRetrievalResponse(BaseModel):
    question: str
    mode: str
    entities: list[EnergyEntity] = Field(default_factory=list)
    relations: list[EnergyRelation] = Field(default_factory=list)
    related_chunk_ids: list[str] = Field(default_factory=list)
    graph_context: str = ""
    chunk_context: str = ""
    final_context: str = ""
    reasoning_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
