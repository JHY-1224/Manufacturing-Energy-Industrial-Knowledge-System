from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse

from app.api.dependencies import bootstrap_legacy_chunks, engine
from app.api.routes_config import router as config_router
from app.api.routes_eval import evaluation_router, router as eval_router
from app.api.routes_experiment import router as experiment_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_graph_retrieval import router as graph_retrieval_router
from app.api.routes_query import router as query_router
from app.api.routes_workbench import router as workbench_router
from app.api.routes_tasks import router as task_router
from app.config.loader import load_config
from app.core.models import IngestRequest, QueryOptions, QueryRequest
from app.evaluation.eval_dataset import EvalDataset
from app.evaluation.experiment_runner import ExperimentRunner, options_for_experiment
from app.evaluation.report_generator import ReportGenerator
from backend.main import CHUNKS, app
from app.runtime import RuntimeAdapterError
from app.storage.knowledge_store import KnowledgeConflictError


@app.exception_handler(RuntimeAdapterError)
async def model_service_error(_request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": exc.code, "stage": exc.stage, "trace_id": getattr(exc, "trace_id", None)})


@app.exception_handler(KnowledgeConflictError)
async def knowledge_conflict(_request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc), "trace_id": getattr(exc, "trace_id", None)})


bootstrap_legacy_chunks(CHUNKS)

app.include_router(config_router)
app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(eval_router)
app.include_router(evaluation_router)
app.include_router(experiment_router)
app.include_router(graph_retrieval_router)
app.include_router(workbench_router)
app.include_router(task_router)


def options_from_config(config: dict, override: dict | None = None) -> QueryOptions:
    pre = config.get("pre_retrieval", {})
    retrieval = config.get("retrieval", {})
    post = config.get("post_retrieval", {})
    generation = config.get("generation", {})
    values = {
        "runtime_mode": config.get("runtime_mode", "offline"),
        "embedding": config.get("embedding", "bge-large-zh"),
        "vectorstore": config.get("vectorstore", "chroma"),
        "retrieval_mode": retrieval.get("mode", "hybrid"),
        "top_k": retrieval.get("top_k", 10),
        "final_top_k": generation.get("final_context_top_k", 5),
        "vector_weight": retrieval.get("vector_weight", 0.7),
        "bm25_weight": retrieval.get("bm25_weight", 0.3),
        "query_rewrite": pre.get("query_rewrite", True),
        "query_expansion": pre.get("query_expansion", True),
        "query_transformation": pre.get("query_transformation", True),
        "metadata_router": pre.get("metadata_router", True),
        "multi_query": pre.get("multi_query", False),
        "hyde": pre.get("hyde", False),
        "rerank": post.get("rerank", True),
        "reranker_model": post.get("reranker_model", "score-fallback"),
        "compression": post.get("compression", True),
        "compression_mode": post.get("compression_mode", "auto"),
        "deduplicate": post.get("deduplicate", True),
        "document_filter": post.get("filter", True),
        "parent_recovery": post.get("parent_recovery", True),
        "rag_fusion": post.get("rag_fusion", False),
    }
    values.update(override or {})
    return QueryOptions.model_validate(values)


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="制造能源工业知识库系统 · 可配置 RAG")
    subparsers = parser.add_subparsers(dest="command")
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--path", required=False)
    ingest_parser.add_argument("--config", default="app/config/rag_config.yaml")
    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--question", required=True)
    query_parser.add_argument("--config", default="app/config/rag_config.yaml")
    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--eval-set", default="data/eval_sets/energy_rag_eval.jsonl")
    eval_parser.add_argument("--config", default="app/config/eval_config.yaml")
    experiment_parser = subparsers.add_parser("experiment")
    experiment_parser.add_argument("--config", default="app/config/experiment_config.yaml")
    experiment_parser.add_argument("--eval-set", default="data/eval_sets/energy_rag_eval.jsonl")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command in {None, "serve"}:
        uvicorn.run("app.main:app", host="127.0.0.1", port=getattr(args, "port", 8000))
        return
    if args.command == "ingest":
        config = load_config(args.config)
        ingest_config = config.get("ingest", {})
        path = args.path or ingest_config.get("path")
        if path:
            paths = [Path(path)]
        else:
            source_dir = Path(ingest_config.get("directory", "data/raw"))
            paths = sorted(item for item in source_dir.rglob("*") if item.is_file() and not item.name.startswith(".")) if source_dir.is_dir() else []
        results = [
            engine.ingest(
                IngestRequest(
                    path=str(item),
                    splitter=ingest_config.get("splitter", "parent_child"),
                    chunk_size=ingest_config.get("chunk_size", 700),
                    chunk_overlap=ingest_config.get("chunk_overlap", 120),
                )
            )
            for item in paths
        ]
        result = {"source_count": len(paths), "chunk_count": sum(item["chunk_count"] for item in results), "items": results}
    elif args.command == "query":
        result = engine.query(QueryRequest(question=args.question, options=options_from_config(load_config(args.config)))).model_dump()
    elif args.command == "eval":
        result = ExperimentRunner(engine).run("cli_eval", EvalDataset.load(args.eval_set), options_from_config(load_config(args.config)), eval_set=args.eval_set)
    else:
        config = load_config(args.config)
        cases = EvalDataset.load(args.eval_set)
        reports = []
        for experiment in config.get("experiments", []):
            overrides = dict(experiment)
            name = overrides.pop("name")
            reports.append(ExperimentRunner(engine).run(name, cases, options_for_experiment(experiment), eval_set=args.eval_set))
        result = {"experiments": reports, "comparison": ReportGenerator().write_comparison(reports)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_cli()
