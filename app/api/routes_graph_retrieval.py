from __future__ import annotations

from time import perf_counter
import hashlib

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.dependencies import engine
from app.core.models import GraphRetrievalRequest, GraphRetrievalResponse, RetrievalHit
from app.storage.workbench_store import WorkbenchStore
from app.graph_retrieval.energy_schema import ENTITY_TYPES, RELATION_TYPES, SUPPORTED_DOMAINS
from app.graph_retrieval.graph_hybrid_retriever import GraphHybridRetriever
from app.graph_retrieval.graph_index_builder import GraphIndexBuilder
from app.graph_retrieval.graph_metrics import calculate_graph_metrics
from app.graph_retrieval.graph_store import GraphStore
from app.knowledge.catalog import validate_base


router = APIRouter(prefix="/api/v2/graph", tags=["Graph-Enhanced Energy Retrieval"])
GRAPH_MODES = {"naive", "local_graph", "global_graph", "hybrid_graph"}


class GraphBuildRequest(BaseModel):
    domain: str | None = None
    rebuild: bool = True


try:
    graph_store = GraphStore.load(engine.store.directory / "graph.json")
except (OSError, ValueError):
    graph_store = GraphStore(engine.store.directory / "graph.json")

_source_signature = None


def _build_graph(domain: str | None = None, rebuild: bool = True) -> dict:
    global _source_signature
    engine.refresh()
    # A source/review change requires removal of expired edges even for a caller
    # requesting incremental construction.
    result = GraphIndexBuilder(graph_store).build_graph_from_chunks(engine.chunks, domain=domain, rebuild=rebuild or _source_signature != _signature())
    _source_signature = None if domain else _signature()
    return result


def _signature():
    return hashlib.sha256("\n".join(sorted(chunk.model_dump_json() for chunk in engine.chunks)).encode()).hexdigest()


def _ensure_graph() -> None:
    engine.refresh()
    if _source_signature != _signature():
        _build_graph()


@router.get("/status")
def graph_status():
    _ensure_graph()
    status = graph_store.status()
    status["supported_domains"] = SUPPORTED_DOMAINS
    status["entity_types"] = sorted(ENTITY_TYPES)
    status["relation_types"] = sorted(RELATION_TYPES)
    status["chunk_source_count"] = len(engine.chunks)
    return status


@router.post("/build")
def build_graph(payload: GraphBuildRequest):
    stats = _build_graph(payload.domain, payload.rebuild)
    return {"status": "completed", **stats}


@router.post("/query", response_model=GraphRetrievalResponse)
def query_graph(payload: GraphRetrievalRequest):
    if payload.options.mode not in GRAPH_MODES:
        raise HTTPException(status_code=422, detail=f"Unsupported graph mode: {payload.options.mode}")
    _ensure_graph()
    started = perf_counter()
    selected_store = graph_store
    if payload.options.knowledge_base_id or payload.options.review_mode != 'reference':
        if payload.options.knowledge_base_id:
            try: validate_base(payload.options.knowledge_base_id)
            except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        selected_store = GraphStore(graph_store.path)
        GraphIndexBuilder(selected_store).build_graph_from_chunks(engine.chunks, persist=False,
            knowledge_base_id=payload.options.knowledge_base_id, review_mode=payload.options.review_mode)
    result = GraphHybridRetriever(selected_store).retrieve(
        payload.question,
        mode=payload.options.mode,
        domain=payload.options.domain,
        max_hops=payload.options.max_hops,
        top_k_entities=payload.options.top_k_entities,
        top_k_relations=payload.options.top_k_relations,
    )
    chunk_by_id = {chunk.chunk_id: chunk for chunk in engine.chunks}
    related_chunks = [chunk_by_id[item] for item in result["related_chunk_ids"] if item in chunk_by_id]
    chunk_context = "\n\n".join(f"[{chunk.chunk_id}] {chunk.content}" for chunk in related_chunks)
    graph_context = result["graph_context"] if payload.options.include_graph_context else ""
    if not payload.options.include_chunk_context:
        chunk_context = ""
    final_context = "\n\n".join(item for item in [graph_context, chunk_context] if item)
    WorkbenchStore(engine.store).assert_sources_current([
        RetrievalHit(doc_id=c.doc_id, chunk_id=c.chunk_id, content=c.content, metadata=c.metadata, score=0)
        for c in related_chunks])
    metrics = calculate_graph_metrics(
        [item.entity_id for item in result["entities"]],
        [item.relation_id for item in result["relations"]],
        result["reasoning_path"],
        result["related_chunk_ids"],
    )
    return GraphRetrievalResponse(
        question=payload.question,
        mode=payload.options.mode,
        entities=result["entities"],
        relations=result["relations"],
        related_chunk_ids=result["related_chunk_ids"],
        graph_context=graph_context,
        chunk_context=chunk_context,
        final_context=final_context,
        reasoning_path=result["reasoning_path"],
        metadata={
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "metrics": metrics,
            "graph_path": graph_store.path.as_posix(),
            "evidence_revision_checked": True,
        },
    )


@router.get("/entities")
def list_entities(
    query: str = Query(default=""),
    domain: str | None = None,
    entity_type: str | None = None,
    top_k: int = Query(default=100, ge=1, le=500),
):
    _ensure_graph()
    if entity_type and entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported entity type: {entity_type}")
    items = graph_store.search_entities(query, domain, top_k, entity_type)
    return {"count": len(items), "items": [item.model_dump() for item in items]}


@router.get("/relations")
def list_relations(
    domain: str | None = None,
    relation_type: str | None = None,
    entity: str | None = None,
):
    _ensure_graph()
    if relation_type and relation_type not in RELATION_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported relation type: {relation_type}")
    entity_id = entity
    if entity and entity not in graph_store.entities:
        matched = graph_store.search_entities(entity, domain, 1)
        entity_id = matched[0].entity_id if matched else "__not_found__"
    items = graph_store.get_relations(entity_id, domain, relation_type)
    return {"count": len(items), "items": [item.model_dump() for item in items]}
