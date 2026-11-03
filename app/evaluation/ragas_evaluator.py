from __future__ import annotations

from app.embeddings.embedding_factory import tokenize
from app.evaluation.ragas_diagnostics import diagnose_sample, diagnose_scores


def _overlap(left: str, right: str) -> float:
    left_terms = set(tokenize(left))
    right_terms = set(tokenize(right))
    return len(left_terms & right_terms) / max(1, len(right_terms))


class RagasEvaluator:
    """Offline-compatible metric proxy with the same output contract as RAGAS."""

    mode = "offline_proxy"
    limitation = "离线词项重合代理评分，未调用 LLM/RAGAS；context_precision 仅为片段词项命中比例，不评价排序或事实正确性。"

    def evaluate(self, question: str, answer: str, contexts: list[str], ground_truth: str | None) -> dict[str, float | None]:
        context = " ".join(contexts)
        context_scores = [_overlap(item, question) for item in contexts]
        metrics: dict[str, float | None] = {
            "faithfulness": round(_overlap(context, answer), 4),
            "answer_relevancy": round(_overlap(answer, question), 4),
            "context_precision": round(sum(score > 0 for score in context_scores) / max(1, len(context_scores)), 4),
            "context_recall": round(_overlap(context, ground_truth or question), 4),
            "answer_correctness": round(_overlap(answer, ground_truth), 4) if ground_truth else None,
            "context_entity_recall": round(_overlap(context, question), 4),
        }
        return metrics

    def diagnose(self, metrics: dict[str, float | None]) -> dict:
        return diagnose_scores(metrics)

    def evaluate_sample(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        ground_truth: str | None,
        retrieved_chunk_ids: list[str] | None = None,
        reference_context_ids: list[str] | None = None,
    ) -> dict:
        metrics = self.evaluate(question, answer, contexts, ground_truth)
        diagnosis = diagnose_sample(
            {key: value for key, value in metrics.items() if value is not None},
            reference_context_ids=reference_context_ids,
            retrieved_chunk_ids=retrieved_chunk_ids,
        )
        return {
            "evaluation_mode": self.mode,
            "evaluation_limitation": self.limitation,
            "metrics": metrics,
            "failure_type": diagnosis.get("primary_failure_type"),
            "root_cause": diagnosis.get("root_cause"),
            "optimization_suggestion": "；".join(diagnosis.get("suggestions", [])),
            "diagnosis": diagnosis,
        }
