from fastapi import APIRouter, HTTPException

from app.api.dependencies import engine
from app.core.models import QueryRequest
from app.storage.knowledge_store import KnowledgeConflictError


router = APIRouter(prefix="/api/v2", tags=["RAG v2 Query"])


@router.post("/query/test")
def query_test(payload: QueryRequest):
    try:
        return engine.query(payload).model_dump()
    except KnowledgeConflictError:
        raise  # The application handler preserves the failed trace ID.
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str):
    trace = engine.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace.model_dump()
