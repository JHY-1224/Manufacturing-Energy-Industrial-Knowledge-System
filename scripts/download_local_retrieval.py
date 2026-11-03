"""Download pinned public BAAI inference assets; never fetch or execute model code."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

MODELS = {
    "BAAI/bge-small-zh-v1.5": "7999e1d3359715c523056ef9478215996d62a620",
    "BAAI/bge-reranker-base": "2cfc18c9415c912f9d8155881c133215df768a70",
}
FILES = {"config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "sentencepiece.bpe.model", "README.md", "LICENSE"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "models": [], "private_data_uploaded": False}
    for repo, revision in MODELS.items():
        with urlopen(f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true", timeout=60) as response:
            metadata = json.load(response)
        if metadata["sha"] != revision or metadata.get("cardData", {}).get("license") != "mit":
            raise ValueError("Official model revision/license differs from the reviewed specification")
        directory = root / repo.replace("/", "--") / revision
        directory.mkdir(parents=True, exist_ok=True)
        model = {"repo": repo, "revision": revision, "license": "MIT", "source": f"https://huggingface.co/{repo}/tree/{revision}", "directory": str(directory.resolve()), "files": []}
        selected = [item for item in metadata["siblings"] if item["rfilename"] in FILES]
        if not {"model.safetensors", "config.json", "tokenizer.json"} <= {item["rfilename"] for item in selected}:
            raise ValueError("Official repository is missing required safetensors/tokenizer assets")
        for item in selected:
            name, expected_size = item["rfilename"], item["size"]
            expected_sha = item.get("lfs", {}).get("sha256")
            target = directory / name
            if target.exists():
                actual_sha = sha256(target)
                if target.stat().st_size != expected_size or (expected_sha and actual_sha != expected_sha):
                    raise ValueError(f"Existing asset differs; refusing to overwrite {repo}/{name}")
            else:
                partial = directory / f"{name}.partial-{uuid4().hex}"
                url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
                print(f"Downloading {repo}/{name}: {expected_size} bytes", flush=True)
                with urlopen(url, timeout=120) as response, partial.open("xb") as handle:
                    for block in iter(lambda: response.read(8 * 1024 * 1024), b""):
                        handle.write(block)
                actual_sha = sha256(partial)
                if partial.stat().st_size != expected_size or (expected_sha and actual_sha != expected_sha):
                    raise ValueError(f"Downloaded asset failed integrity check: {repo}/{name}")
                # This script does not replace existing assets or remove failed evidence.
                if target.exists():
                    raise FileExistsError(target)
                partial.rename(target)
            model["files"].append({"name": name, "size": expected_size, "sha256": actual_sha, "official_lfs_sha256": expected_sha})
            print(f"Verified {repo}/{name}", flush=True)
        manifest["models"].append(model)
    output = root / f"retrieval-download-{uuid4().hex}.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(f"Manifest: {output}", flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, default=Path(__file__).resolve().parents[1] / "data/runtime/models")
    args = parser.parse_args()
    download(args.models_root)
