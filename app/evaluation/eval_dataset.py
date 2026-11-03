import json
import math
from pathlib import Path

from app.core.models import EvalCase


class EvalDataset:
    @staticmethod
    def normalize(raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise ValueError("Each evaluation sample must be a JSON object")
        data = dict(raw)
        if "query" not in data and "question" in data:
            data["query"] = data["question"]
        if "query_id" not in data and "eval_id" in data:
            data["query_id"] = data["eval_id"]
        if "category" not in data:
            data["category"] = data.get("domain") or data.get("doc_type") or "evaluation"
        if "gold_doc_ids" not in data and "reference_context_ids" in data:
            data["gold_doc_ids"] = data["reference_context_ids"]
        return data

    @staticmethod
    def load(path: str | Path) -> list[EvalCase]:
        return EvalDataset.loads(Path(path).read_text(encoding="utf-8-sig"))

    @staticmethod
    def loads(text: str) -> list[EvalCase]:
        cases: list[EvalCase] = []
        seen: set[str] = set()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                try:
                    case = EvalCase.model_validate(EvalDataset.normalize(json.loads(line)))
                    if not case.query_id.strip() or not case.query.strip():
                        raise ValueError("query_id and query must be non-empty")
                    if case.query_id in seen:
                        raise ValueError("Duplicate query_id")
                    if any(not math.isfinite(value) or not 0 <= value <= 100 for value in case.relevance.values()):
                        raise ValueError("Relevance grades must be finite numbers between 0 and 100")
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Invalid evaluation sample on line {line_number}") from exc
                seen.add(case.query_id)
                cases.append(case)
        return cases
