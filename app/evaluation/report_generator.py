from __future__ import annotations

import csv
import json
import re
import os
from pathlib import Path
from uuid import uuid4


def validate_experiment_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Experiment name must contain 1-120 characters")
    if any(char in name for char in ("/", "\\", "\x00", "\n", "\r")):
        raise ValueError("Experiment name must not contain paths or control characters")
    return name.strip()


class ReportGenerator:
    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else Path(os.environ.get("RAG_REPORT_DIR", str(Path(__file__).resolve().parents[2] / "reports/eval_reports")))

    def _paths(self, identifier: str) -> dict[str, Path]:
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier):
            raise ValueError("Invalid report identifier")
        paths = {key: self.output_dir / f"{identifier}.{suffix}" for key, suffix in (("json", "json"), ("csv", "csv"), ("markdown", "md"))}
        if any(path.exists() or path.is_symlink() for path in paths.values()):
            raise FileExistsError("Report already exists")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return paths

    def write(self, name: str, report: dict) -> dict[str, str]:
        name = validate_experiment_name(name)
        report.setdefault("run_id", f"ragas_run_{uuid4().hex}")
        paths = self._paths(report["run_id"])
        report["report_files"] = {key: str(path) for key, path in paths.items()}
        serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        metrics = report.get("metrics", {})
        with paths["csv"].open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "value"])
            writer.writerows(metrics.items())
        lines = [f"# {name}", "", f"Evaluation mode: {report.get('evaluation_mode', 'unspecified')}", "", "## Metrics", "", "| Metric | Value |", "|---|---:|"]
        lines.extend(f"| {key} | {value} |" for key, value in metrics.items())
        lines.extend(["", "## Failed Cases", ""])
        for item in report.get("failed_cases", []):
            question = item.get("query") or item.get("question")
            reason = item.get("reason") or item.get("root_cause") or item.get("failure_type")
            lines.append(f"- `{item.get('query_id')}` {question} - {reason}")
        with paths["markdown"].open("x", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        # The JSON is the discoverable completion record; never replace an older run.
        with paths["json"].open("x", encoding="utf-8") as handle:
            handle.write(serialized)
        return report["report_files"]

    def write_comparison(self, reports: list[dict], name: str = "experiment_comparison") -> dict:
        name = validate_experiment_name(name)
        rows = []
        for report in reports:
            metrics = report.get("metrics", {})
            weights = {"recall_at_5": 0.30, "mrr": 0.20, "faithfulness": 0.20, "context_precision": 0.15, "answer_correctness": 0.15}
            # Missing ground truth or retrieval labels is not a zero measured score.
            quality_score = sum(metrics[key] * weight for key, weight in weights.items()) if all(metrics.get(key) is not None for key in weights) else None
            rows.append(
                {
                    "experiment": report.get("experiment", "unknown"),
                    "run_id": report.get("run_id"),
                    "evaluation_mode": report.get("evaluation_mode", "unspecified"),
                    "quality_score": round(quality_score, 4) if quality_score is not None else None,
                    "recall_at_5": metrics.get("recall_at_5"),
                    "mrr": metrics.get("mrr"),
                    "ndcg_at_5": metrics.get("ndcg_at_5"),
                    "faithfulness": metrics.get("faithfulness"),
                    "context_precision": metrics.get("context_precision"),
                    "answer_correctness": metrics.get("answer_correctness"),
                    "latency_total_ms": metrics.get("latency_total_ms"),
                }
            )
        rows.sort(key=lambda item: item["quality_score"] if item["quality_score"] is not None else -1, reverse=True)
        comparison = {"comparison_id": f"comparison_{uuid4().hex}", "name": name, "recommended_experiment": rows[0]["experiment"] if rows and rows[0]["quality_score"] is not None else None, "experiments": rows}
        paths = self._paths(comparison["comparison_id"])
        comparison["report_files"] = {key: str(path) for key, path in paths.items()}
        serialized = json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False)
        fieldnames = list(rows[0]) if rows else ["experiment", "quality_score"]
        with paths["csv"].open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        lines = [f"# {name}", "", f"Recommended: `{comparison['recommended_experiment']}`", ""]
        if rows:
            lines.extend(["| " + " | ".join(fieldnames) + " |", "|" + "---|" * len(fieldnames)])
            lines.extend("| " + " | ".join(str(row.get(key, "")) for key in fieldnames) + " |" for row in rows)
        with paths["markdown"].open("x", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        with paths["json"].open("x", encoding="utf-8") as handle:
            handle.write(serialized)
        return comparison
