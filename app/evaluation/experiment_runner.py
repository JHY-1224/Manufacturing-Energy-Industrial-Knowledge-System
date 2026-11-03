from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean
from pathlib import Path
from uuid import uuid4

from app.core.models import QueryOptions, QueryRequest
from app.evaluation.ragas_evaluator import RagasEvaluator
from app.evaluation.report_generator import ReportGenerator, validate_experiment_name
from app.evaluation.retrieval_metrics import calculate_retrieval_metrics


def options_for_experiment(experiment: dict) -> QueryOptions:
    if not isinstance(experiment, dict):
        raise ValueError("Each experiment must be an object")
    validate_experiment_name(experiment.get("name"))
    unknown = set(experiment) - {"name", *QueryOptions.model_fields}
    if unknown:
        raise ValueError("Unknown experiment option")
    values = {"query_transformation": False, **{key: value for key, value in experiment.items() if key != "name"}}
    return QueryOptions.model_validate(values)


def reference_kind(reference_ids: list[str], chunks: list) -> str:
    documents = {chunk.doc_id for chunk in chunks}
    children = {chunk.chunk_id for chunk in chunks}
    references = set(reference_ids)
    if references & documents & children:
        raise ValueError("Reference ID is ambiguous between document and chunk")
    document_refs = references & documents
    chunk_refs = references & children
    if document_refs and chunk_refs:
        raise ValueError("A sample must not mix document and chunk reference IDs")
    return "chunk" if chunk_refs else "document" if document_refs else "unresolved"


class ExperimentRunner:
    def __init__(self, engine, output_dir: str | Path | None = None) -> None:
        self.engine = engine
        self.output_dir = output_dir

    def run(self, name: str, cases: list, options: QueryOptions, write_report: bool = True, eval_set: str | None = None) -> dict:
        name = validate_experiment_name(name)
        if not cases:
            raise ValueError("Evaluation dataset contains no cases")
        if hasattr(self.engine, "refresh"):
            self.engine.refresh()
        corpus_snapshot = list(self.engine.chunks)
        kinds = [reference_kind(case.gold_doc_ids, corpus_snapshot) for case in cases]
        options = options.model_copy(deep=True)
        started_at = datetime.now()
        run_id = f"ragas_run_{uuid4().hex}"
        totals: dict[str, list[float]] = defaultdict(list)
        category_totals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        failed_cases: list[dict] = []
        sample_results: list[dict] = []
        traces = []
        evaluator = RagasEvaluator()
        for case, kind in zip(cases, kinds):
            trace = self.engine.query(QueryRequest(question=case.query, options=options))
            traces.append(trace.trace_id)
            retrieved_doc_ids = list(dict.fromkeys(hit.doc_id for hit in trace.reranked_docs))
            retrieved_chunk_ids = list(dict.fromkeys(hit.chunk_id for hit in trace.reranked_docs))
            reference_ids = list(case.gold_doc_ids)
            retrieved_for_reference = retrieved_chunk_ids if kind == "chunk" else retrieved_doc_ids if kind == "document" else []
            contexts = trace.compressed_context or [hit.content for hit in trace.reranked_docs]
            retrieval_metrics = calculate_retrieval_metrics(retrieved_for_reference, reference_ids, case.relevance) if reference_ids else {}
            sample_eval = evaluator.evaluate_sample(
                case.query,
                trace.answer,
                contexts,
                case.ground_truth,
                retrieved_chunk_ids=retrieved_for_reference,
                reference_context_ids=reference_ids,
            )
            if reference_ids and not retrieval_metrics.get("hit_at_5", 0.0) and not sample_eval.get("failure_type"):
                sample_eval.update({
                    "failure_type": "retrieval_miss",
                    "root_cause": "参考 document/chunk 未在 Top5 中命中。",
                    "optimization_suggestion": "核对参考 ID、索引内容与检索配置。",
                })
            ragas_metrics = sample_eval["metrics"]
            for key, value in {**retrieval_metrics, **ragas_metrics}.items():
                if value is not None:
                    totals[key].append(float(value))
                    category_totals[case.category][key].append(float(value))
            for key, value in trace.latency_ms.items():
                totals[f"latency_{key}_ms"].append(value)
            for token_key in ("input_tokens", "output_tokens"):
                if trace.token_usage.get(token_key) is not None:
                    totals[token_key].append(trace.token_usage[token_key])
            if trace.cost.get("total") is not None:
                totals["cost_total"].append(trace.cost["total"])
            sample_result = {
                "run_id": run_id,
                "evaluation_mode": evaluator.mode,
                "eval_id": case.query_id,
                "question": case.query,
                "ground_truth": case.ground_truth,
                "retrieved_contexts": contexts,
                "retrieved_chunk_ids": retrieved_chunk_ids,
                "reference_context_ids": reference_ids,
                "reference_id_kind": kind,
                "retrieval_metrics": retrieval_metrics,
                "generated_answer": trace.answer,
                "answer": trace.answer,
                "metrics": ragas_metrics,
                "faithfulness": ragas_metrics.get("faithfulness"),
                "answer_relevancy": ragas_metrics.get("answer_relevancy"),
                "context_precision": ragas_metrics.get("context_precision"),
                "context_recall": ragas_metrics.get("context_recall"),
                "failure_type": sample_eval.get("failure_type"),
                "root_cause": sample_eval.get("root_cause"),
                "optimization_suggestion": sample_eval.get("optimization_suggestion"),
                "source_chunks": retrieved_chunk_ids,
                "trace": {
                    "trace_id": trace.trace_id,
                    "retrieval_mode": trace.retrieval_mode,
                    "latency_ms": trace.latency_ms,
                    "tokens": trace.token_usage,
                },
            }
            sample_results.append(sample_result)
            if sample_eval.get("failure_type") or (reference_ids and not retrieval_metrics.get("hit_at_5", 0.0)):
                failed_cases.append(
                    {
                        "query_id": case.query_id,
                        "eval_id": case.query_id,
                        "run_id": run_id,
                        "evaluation_mode": evaluator.mode,
                        "question": case.query,
                        "domain": case.category,
                        "ground_truth": case.ground_truth,
                        "actual_answer": trace.answer,
                        "expected_chunk_ids": reference_ids,
                        "retrieved_chunk_ids": retrieved_chunk_ids,
                        "faithfulness": ragas_metrics.get("faithfulness"),
                        "answer_relevancy": ragas_metrics.get("answer_relevancy"),
                        "context_precision": ragas_metrics.get("context_precision"),
                        "context_recall": ragas_metrics.get("context_recall"),
                        "failure_type": sample_eval.get("failure_type") or "retrieval_miss",
                        "root_cause": sample_eval.get("root_cause") or "gold document not found in Top5",
                        "optimization_suggestion": sample_eval.get("optimization_suggestion") or "检查检索配置和评测集 reference_context_ids。",
                    }
                )
        finished_at = datetime.now()
        metrics = {key: round(mean(values), 4) for key, values in totals.items() if values}
        report = {
            "run_id": run_id,
            "experiment": name,
            "eval_dataset": eval_set,
            "evaluation_mode": evaluator.mode,
            "evaluation_limitation": evaluator.limitation,
            "embedding_model": options.embedding,
            "vector_db": options.vectorstore,
            "retriever_config": options.retrieval_mode,
            "retrieval_config": options.retrieval_mode,
            "reranker_model": options.reranker_model if options.rerank else "disabled",
            "top_k": options.top_k,
            "score_threshold": None,
            "started_at": started_at.isoformat(timespec="microseconds"),
            "finished_at": finished_at.isoformat(timespec="microseconds"),
            "status": "completed",
            "case_count": len(cases),
            "sample_count": len(cases),
            "options": options.model_dump(),
            "metrics": metrics,
            "avg_faithfulness": metrics.get("faithfulness"),
            "avg_answer_relevancy": metrics.get("answer_relevancy"),
            "avg_context_precision": metrics.get("context_precision"),
            "avg_context_recall": metrics.get("context_recall"),
            "category_metrics": {
                category: {key: round(mean(values), 4) for key, values in metrics.items() if values}
                for category, metrics in category_totals.items()
            },
            "sample_results": sample_results,
            "failed_cases": failed_cases,
            "trace_ids": traces,
        }
        report["report_files"] = ReportGenerator(self.output_dir).write(name, report) if write_report else {}
        return report
