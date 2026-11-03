import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def runtime_dir() -> Path:
    return Path(os.environ.get("RAG_DATA_DIR", str(PROJECT_ROOT / "data" / "runtime"))).expanduser().resolve()
