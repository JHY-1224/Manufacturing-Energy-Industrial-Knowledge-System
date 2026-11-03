from pathlib import Path

from fastapi import APIRouter, HTTPException
import yaml

from app.api.dependencies import engine
from app.api import routes_eval
from app.evaluation.experiment_runner import ExperimentRunner, options_for_experiment
from app.evaluation.report_generator import ReportGenerator


router = APIRouter(prefix="/api/v2/experiments", tags=["RAG v2 Experiments"])
CONFIG_DIR = routes_eval.PROJECT_ROOT / "app/config"


@router.post("/run")
def run_experiments(config_path: str = "app/config/experiment_config.yaml", eval_set: str = "data/eval_sets/energy_rag_eval.jsonl"):
    target = Path(config_path)
    target = (target if target.is_absolute() else routes_eval.PROJECT_ROOT / target).resolve()
    if CONFIG_DIR.resolve() not in target.parents or target.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise HTTPException(status_code=400, detail="Experiment config must be inside the configured config directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Experiment config not found")
    try:
        config = yaml.safe_load(target.read_text(encoding="utf-8"))
        experiments = config.get("experiments") if isinstance(config, dict) else None
        if not isinstance(experiments, list) or not experiments:
            raise ValueError("A non-empty experiments list is required")
        presets = [(item["name"], options_for_experiment(item)) for item in experiments]
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail="Invalid experiment configuration") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Experiment configuration cannot be read") from exc
    dataset = routes_eval._resolve_eval_set(eval_set)
    cases = routes_eval._load_cases(dataset)
    try:
        reports = [ExperimentRunner(engine, output_dir=routes_eval.REPORT_DIR).run(name, cases, options, eval_set=str(dataset)) for name, options in presets]
        return {"experiments": reports, "comparison": ReportGenerator(routes_eval.REPORT_DIR).write_comparison(reports)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Report already exists") from exc
