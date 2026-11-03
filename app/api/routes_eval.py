from pathlib import Path
import json
import math
import os

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api.dependencies import engine
from app.core.models import EvalRunRequest
from app.evaluation.eval_dataset import EvalDataset
from app.evaluation.experiment_runner import ExperimentRunner
from app.evaluation.ragas_diagnostics import (
    EVALUATION_DATASETS, EVALUATION_RUNS, SAMPLE_RESULTS, RAGAS_METRIC_KEYS,
    build_evaluation_summary, diagnose_scores,
)


router = APIRouter(prefix="/api/v2/eval", tags=["RAG v2 Evaluation"])
evaluation_router = APIRouter(prefix="/api/v1/evaluation", tags=["RAGAS Evaluation Center"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(os.environ.get("RAG_EVAL_DIR", str(PROJECT_ROOT / "data/eval_sets")))
REPORT_DIR = Path(os.environ.get("RAG_REPORT_DIR", str(PROJECT_ROOT / "reports/eval_reports")))


def _safe_filename(filename: str, suffix: str) -> str:
    if (not isinstance(filename, str) or not filename or len(filename) > 180
            or filename.startswith(".") or Path(filename).name != filename
            or any(char in filename for char in ("/", "\\"))
            or any(ord(char) < 32 for char in filename)
            or Path(filename).suffix.lower() != suffix):
        raise HTTPException(status_code=400, detail=f"Invalid {suffix} filename")
    return Path(filename).stem + suffix


def _resolve_eval_set(path: str) -> Path:
    target = Path(path)
    if not target.is_absolute():
        # Keep the established project-relative contract, independent of process cwd.
        target = PROJECT_ROOT / target
    target = target.resolve()
    if EVAL_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="Evaluation set must be inside the configured dataset directory")
    if target.suffix.lower() != ".jsonl":
        raise HTTPException(status_code=400, detail="Only .jsonl evaluation sets are supported")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Evaluation set not found")
    return target


def _load_cases(target: Path):
    try:
        cases = EvalDataset.load(target)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSONL evaluation samples") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Evaluation set cannot be read") from exc
    if not cases:
        raise HTTPException(status_code=422, detail="Evaluation set contains no cases")
    return cases


def _dataset_record(target: Path, include_samples: bool = False) -> dict:
    cases = _load_cases(_resolve_eval_set(str(target.resolve())))
    result = {
        "dataset_id": target.stem, "name": target.stem, "eval_set": str(target.resolve()),
        "sample_count": len(cases), "domain": ", ".join(sorted({case.category for case in cases})),
        "data_source": "file", "status": "ready",
    }
    if include_samples:
        result["samples"] = [case.model_dump() for case in cases]
    return result


def _dataset_records() -> list[dict]:
    records = []
    for target in sorted(EVAL_DIR.glob("*.jsonl")):
        try:
            records.append(_dataset_record(target))
        except HTTPException:
            records.append({"dataset_id": target.stem, "name": target.stem, "sample_count": None,
                            "data_source": "file", "status": "invalid", "error": "Dataset cannot be validated"})
    return records


def _load_report_payloads() -> list[dict]:
    payloads = []
    for path in REPORT_DIR.glob("*.json"):
        try:
            if path.resolve().parent != REPORT_DIR.resolve():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            json.dumps(payload, allow_nan=False)
            if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
                continue
            if not isinstance(payload.get("run_id") or payload.get("experiment"), str):
                continue
            if not isinstance(payload.get("sample_results", []), list) or not isinstance(payload.get("failed_cases", []), list):
                continue
            if any(not isinstance(item, dict) for key in ("sample_results", "failed_cases") for item in payload.get(key, [])):
                continue
            if not isinstance(payload.get("options", {}), dict):
                continue
            payloads.append(payload)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(payloads, key=lambda item: str(item.get("started_at") or ""), reverse=True)


def _run_record_from_report(payload: dict) -> dict:
    metrics = payload.get("metrics", {})
    options = payload.get("options", {})
    result = {
        "run_id": payload.get("run_id") or payload.get("experiment"),
        "experiment": payload.get("experiment"), "eval_dataset": payload.get("eval_dataset"),
        "embedding_model": payload.get("embedding_model", options.get("embedding")),
        "vector_db": payload.get("vector_db", options.get("vectorstore")),
        "retriever_config": payload.get("retriever_config", options.get("retrieval_mode")),
        "retrieval_config": payload.get("retrieval_config", options.get("retrieval_mode")),
        "reranker_model": payload.get("reranker_model", options.get("reranker_model")),
        "top_k": payload.get("top_k", options.get("top_k")), "score_threshold": payload.get("score_threshold"),
        "started_at": payload.get("started_at"), "finished_at": payload.get("finished_at"),
        "status": payload.get("status", "completed"),
        "sample_count": payload.get("sample_count", payload.get("case_count")),
        "report_files": payload.get("report_files", {}), "metrics": metrics, "options": options,
        "data_source": "stored_report", "evaluation_mode": payload.get("evaluation_mode", "unspecified"),
        "evaluation_limitation": payload.get("evaluation_limitation"),
    }
    result.update({f"avg_{key}": payload.get(f"avg_{key}", metrics.get(key)) for key in RAGAS_METRIC_KEYS})
    return result


def _combined_runs(demo: bool = False) -> list[dict]:
    runs = [_run_record_from_report(payload) for payload in _load_report_payloads()]
    seen = {item["run_id"] for item in runs}
    if demo:
        runs.extend({**item, "data_source": "demo", "evaluation_mode": "demo"} for item in EVALUATION_RUNS if item["run_id"] not in seen)
    return runs


def _find_report(run_id: str) -> dict | None:
    return next((item for item in _load_report_payloads() if (item.get("run_id") or item.get("experiment")) == run_id), None)


def _write_dataset(target: Path, cases: list) -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write("\n".join(case.model_dump_json() for case in cases) + "\n")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Evaluation dataset already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Evaluation dataset cannot be saved") from exc


@router.post("/run")
def run_evaluation(payload: EvalRunRequest):
    target = _resolve_eval_set(payload.eval_set)
    cases = _load_cases(target)
    try:
        return ExperimentRunner(engine, output_dir=REPORT_DIR).run(payload.experiment_name, cases, payload.options, eval_set=str(target))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Report already exists") from exc


@router.post("/upload")
async def upload_eval_set(request: Request, filename: str = Query(...)):
    safe_name = _safe_filename(filename, ".jsonl")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Evaluation file is empty")
    try:
        cases = EvalDataset.loads(content.decode("utf-8-sig"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSONL evaluation set") from exc
    if not cases:
        raise HTTPException(status_code=422, detail="Evaluation set contains no cases")
    target = EVAL_DIR / safe_name
    _write_dataset(target, cases)
    return {"dataset_id": target.stem, "eval_set": str(target.resolve()), "case_count": len(cases), "filename": safe_name}


@router.get("/reports/{filename}")
def download_report(filename: str):
    suffix = Path(filename).suffix.lower()
    if suffix not in {".json", ".csv", ".md"}:
        raise HTTPException(status_code=400, detail="Unsupported report format")
    safe_name = _safe_filename(filename, suffix)
    target = (REPORT_DIR / safe_name).resolve()
    if target.parent != REPORT_DIR.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(target, filename=safe_name)


@evaluation_router.get("/datasets")
def list_evaluation_datasets(demo: bool = False):
    items = _dataset_records()
    if demo:
        known = {item["dataset_id"] for item in items}
        items.extend({**item, "data_source": "demo"} for item in EVALUATION_DATASETS if item["dataset_id"] not in known)
    return {"items": items}


@evaluation_router.post("/datasets")
def create_evaluation_dataset(payload: dict = Body(...)):
    dataset_id = payload.get("dataset_id") or payload.get("name")
    samples = payload.get("samples")
    if not isinstance(dataset_id, str) or not dataset_id.strip() or not isinstance(samples, list) or not samples:
        raise HTTPException(status_code=400, detail="dataset_id and non-empty samples are required")
    safe_name = _safe_filename(f"{dataset_id.strip()}.jsonl", ".jsonl")
    try:
        cases = EvalDataset.loads("\n".join(json.dumps(sample, ensure_ascii=False, allow_nan=False) for sample in samples))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid evaluation samples") from exc
    target = EVAL_DIR / safe_name
    _write_dataset(target, cases)
    return {"dataset_id": target.stem, "eval_set": str(target.resolve()), "sample_count": len(cases)}


@evaluation_router.get("/datasets/{dataset_id}")
def get_evaluation_dataset(dataset_id: str, demo: bool = False):
    target = EVAL_DIR / _safe_filename(f"{dataset_id}.jsonl", ".jsonl")
    if target.is_file():
        return _dataset_record(target, include_samples=True)
    if demo:
        item = next((item for item in EVALUATION_DATASETS if item["dataset_id"] == dataset_id), None)
        if item:
            return {**item, "data_source": "demo"}
    raise HTTPException(status_code=404, detail="Evaluation dataset not found")


@evaluation_router.post("/run")
def run_evaluation_v1(payload: EvalRunRequest):
    return run_evaluation(payload)


@evaluation_router.get("/runs")
def list_evaluation_runs(demo: bool = False):
    return {"items": _combined_runs(demo)}


@evaluation_router.get("/runs/{run_id}")
def get_evaluation_run(run_id: str, demo: bool = False):
    payload = _find_report(run_id)
    if payload is not None:
        return {**_run_record_from_report(payload), "report": payload}
    if demo:
        item = next((item for item in EVALUATION_RUNS if item["run_id"] == run_id), None)
        if item:
            return {**item, "data_source": "demo", "evaluation_mode": "demo"}
    raise HTTPException(status_code=404, detail="Evaluation run not found")


@evaluation_router.get("/runs/{run_id}/samples")
def list_run_samples(run_id: str, demo: bool = False):
    run = get_evaluation_run(run_id, demo)
    return {"items": run["report"].get("sample_results", []) if "report" in run else [dict(item, data_source="demo") for item in SAMPLE_RESULTS if item["run_id"] == run_id]}


@evaluation_router.get("/runs/{run_id}/failures")
def list_run_failures(run_id: str, demo: bool = False):
    run = get_evaluation_run(run_id, demo)
    if "report" in run:
        payload = run["report"]
        return {"items": payload.get("failed_cases", [item for item in payload.get("sample_results", []) if item.get("failure_type")])}
    return {"items": [dict(item, data_source="demo") for item in SAMPLE_RESULTS if item["run_id"] == run_id and item.get("failure_type")]}


@evaluation_router.get("/metrics/summary")
def get_metrics_summary(demo: bool = False):
    reports = _load_report_payloads()
    return build_evaluation_summary([_run_record_from_report(item) for item in reports], reports=reports, datasets=_dataset_records(), demo=demo)


@evaluation_router.post("/diagnose")
def diagnose_evaluation_scores(payload: dict = Body(...)):
    for key in RAGAS_METRIC_KEYS:
        value = payload.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1):
            raise HTTPException(status_code=422, detail="Metric scores must be finite numbers between 0 and 1")
    return diagnose_scores(payload)
