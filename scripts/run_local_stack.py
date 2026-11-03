"""Start the verified Mac MLX + CPU retrieval stack; Ctrl-C stops only owned processes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CHAT_PORT, RETRIEVAL_PORT, VISION_PORT = 15101, 8082, 8083


def local_environment():
    # Explicit local profile; never read or modify .env / cloud credentials.
    return {**os.environ,
        "RAG_CHAT_URL": f"http://127.0.0.1:{CHAT_PORT}/v1/chat/completions",
        "RAG_CHAT_MODEL": "qwen3.5-9b-mlx-4bit", "RAG_CHAT_API_KEY": "",
        "RAG_EMBEDDING_URL": f"http://127.0.0.1:{RETRIEVAL_PORT}/v1/embeddings",
        "RAG_EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5", "RAG_EMBEDDING_DIMENSIONS": "512",
        "RAG_EMBEDDING_REVISION": "7999e1d3359715c523056ef9478215996d62a620:token_chunks_weighted_mean_l2_v1",
        "RAG_EMBEDDING_API_KEY": "", "RAG_EMBEDDING_RESPONSE_MODEL": "BAAI/bge-small-zh-v1.5",
        "RAG_EMBEDDING_SEND_DIMENSIONS": "false",
        "RAG_RERANK_URL": f"http://127.0.0.1:{RETRIEVAL_PORT}/api/v1/services/rerank/text-rerank/text-rerank",
        "RAG_RERANK_MODEL": "BAAI/bge-reranker-base", "RAG_RERANK_API_KEY": "",
        "RAG_RERANK_RESPONSE_MODEL": "BAAI/bge-reranker-base",
        "RAG_CHAT_RESPONSE_MODEL": "qwen3.5-9b-mlx-4bit",
        "RAG_VISION_URL": f"http://127.0.0.1:{VISION_PORT}",
        "RAG_MODEL_TIMEOUT_SECONDS": "120", "RAG_MODEL_MAX_OUTPUT_TOKENS": "1024",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}


def healthy(kind, port):
    try:
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return False
    if kind == "vision":
        return payload.get("status")=="ready" and payload.get("local_only") is True and payload.get("training") is False and payload.get("embedding_model")=="google/siglip2-base-patch16-224" and payload.get("vision_model")=="Qwen3-VL-8B-Instruct/MLX4"
    if kind == "chat":
        return payload.get("status") == "ready" and payload.get("model") == "qwen3.5-9b-mlx-4bit" and payload.get("text_only") is True and payload.get('streaming') is True
    return (payload.get("offline_models") is True and payload.get("embedding_model") == "BAAI/bge-small-zh-v1.5"
            and payload.get("embedding_revision") == "7999e1d3359715c523056ef9478215996d62a620"
            and payload.get("reranker_revision") == "2cfc18c9415c912f9d8155881c133215df768a70"
            and payload.get("embedding_strategy") == "token_chunks_weighted_mean_l2_v1"
            and payload.get("rerank_strategy") == "document_token_chunks_max_v1")


def require_free(port):
    with socket.socket() as probe:
        if os.name == "posix":
            # Match Uvicorn's bind behavior: a closed listener's TIME_WAIT is reusable.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} is occupied by an unrecognized service; no process was stopped") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend-only", action="store_true", help="Require existing verified model services")
    parser.add_argument("--check", action="store_true", help="Check the two model services without starting anything")
    parser.add_argument("--background", action="store_true", help="Keep the local stack alive after this terminal/tool call exits; logs are retained")
    parser.add_argument("--with-vision", action="store_true",help="Also start/reuse the optional local pretrained vision worker")
    parser.add_argument("--vision-embedding",type=Path,default=ROOT/'data/runtime/models/google--siglip2-base-patch16-224/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2')
    parser.add_argument("--vision-model",type=Path,default=ROOT/'data/runtime/models/qwen3-vl-8b-mlx-4bit')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port in (CHAT_PORT, RETRIEVAL_PORT, VISION_PORT):
        parser.error("Backend port must be 1024-65535 and different from model ports")
    if args.check:
        checks = {"mlx_chat": healthy("chat", CHAT_PORT), "local_retrieval": healthy("retrieval", RETRIEVAL_PORT)}
        if args.with_vision:checks["local_vision"]=healthy("vision",VISION_PORT)
        print(json.dumps(checks)); return 0 if all(checks.values()) else 1
    if args.background:
        require_free(args.port)
        directory=ROOT/'data/runtime/service-logs';directory.mkdir(parents=True,exist_ok=True)
        identity=uuid4().hex;log=directory/('stack-'+identity+'.log')
        command=[str(ROOT/'.venv-retrieval/bin/python'),str(Path(__file__).resolve()),'--port',str(args.port)]
        if args.backend_only:command.append('--backend-only')
        if args.with_vision:command.extend(['--with-vision','--vision-embedding',str(args.vision_embedding),'--vision-model',str(args.vision_model)])
        with log.open('x') as handle:
            process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=handle,stderr=handle,start_new_session=True)
        record={'pid':process.pid,'port':args.port,'log':str(log),'project':str(ROOT),'command':command}
        (directory/('stack-'+identity+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(record,ensure_ascii=False));return 0
    python_mlx, python_backend = ROOT/".venv-mlx/bin/python", ROOT/".venv-retrieval/bin/python"
    children, handles = [], []
    required_children, optional_children = [], []
    reported_optional = set()
    env = local_environment()
    try:
        require_free(args.port)
        if not python_backend.is_file():
            raise RuntimeError("Missing .venv-retrieval; follow the pinned setup in AGENTS.md")
        model_commands = [
            ("chat", CHAT_PORT, [str(python_mlx), str(ROOT/"scripts/local_model/serve_mlx.py"), "--port", str(CHAT_PORT)]),
            ("retrieval", RETRIEVAL_PORT, [str(python_backend), str(ROOT/"scripts/serve_local_retrieval.py"), "--port", str(RETRIEVAL_PORT)]),
        ]
        if args.with_vision:model_commands.append(("vision",VISION_PORT,[str(ROOT/'.venv-vision/bin/python'),'-m','scripts.serve_local_vision_limited','--embedding',str(args.vision_embedding),'--vision',str(args.vision_model),'--port',str(VISION_PORT)]))
        for kind, port, command in model_commands:
            if healthy(kind, port):
                print(f"Reusing verified {kind} service on 127.0.0.1:{port}", flush=True); continue
            if args.backend_only:
                raise RuntimeError(f"Required {kind} service is not ready")
            require_free(port)
            if not Path(command[0]).is_file():
                raise RuntimeError(f"Missing interpreter for {kind}; follow AGENTS.md")
            log_dir = ROOT/"data/runtime/service-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            handle = (log_dir/f"{kind}-{uuid4().hex}.log").open("x")
            handles.append(handle)
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=handle)
            children.append(process)
            (optional_children if kind=='vision' else required_children).append(process)
            deadline = time.monotonic()+120
            while not healthy(kind, port):
                if process.poll() is not None or time.monotonic() >= deadline:
                    if kind=='vision':
                        print(f"Optional vision service unavailable; text backend remains available. Inspect {handle.name}",flush=True)
                        reported_optional.add(process.pid);break
                    raise RuntimeError(f"{kind} startup failed; inspect {handle.name}")
                time.sleep(0.5)
        print(f"Backend http://127.0.0.1:{args.port}; select configured in the retrieval page. Ctrl-C stops owned processes.", flush=True)
        backend = subprocess.Popen([str(python_backend), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)], cwd=ROOT, env=env)
        children.append(backend)
        while backend.poll() is None:
            if any(child.poll() is not None for child in required_children):
                raise RuntimeError("An owned required model service stopped; stopping this backend")
            for child in optional_children:
                if child.poll() is not None and child.pid not in reported_optional:
                    print('Optional vision worker stopped; text backend remains available. Vision requests will report unavailable.',flush=True)
                    reported_optional.add(child.pid)
            time.sleep(0.5)
        return backend.returncode
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, OSError) as exc:
        print(str(exc)); return 1
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        for handle in handles: handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
