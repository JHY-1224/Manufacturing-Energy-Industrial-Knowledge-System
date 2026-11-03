"""Opt-in, synthetic-only HTTP verification of the local P1 stack (three model answers)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.storage.knowledge_store import KnowledgeStore
    from scripts.run_local_stack import require_free
    port = 18000
    require_free(port)
    root = ROOT/"data/runtime/validation"/uuid4().hex
    root.mkdir(parents=True)
    store = KnowledgeStore(root/"knowledge")
    store.set_meta("initialized", "1")  # Keep this verification corpus entirely synthetic and isolated.
    env = {**os.environ, "RAG_DATA_DIR": str(root/"knowledge"), "RAG_EVAL_DIR": str(root/"eval"), "RAG_REPORT_DIR": str(root/"reports"), "PYTHONDONTWRITEBYTECODE": "1"}
    opener = build_opener(ProxyHandler({}))
    log = (root/"backend.log").open("x")
    process = None

    def call(path, payload=None, method=None, raw=None):
        data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = Request(f"http://127.0.0.1:{port}"+path, data=data, method=method,
                          headers={"Content-Type": "application/octet-stream" if raw is not None else "application/json"})
        with opener.open(request, timeout=180) as response:
            return json.load(response)

    def start():
        child = subprocess.Popen([str(ROOT/".venv-retrieval/bin/python"), "-B", str(ROOT/"scripts/run_local_stack.py"), "--backend-only", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
        deadline = time.monotonic()+30
        while True:
            try:
                call("/api/health"); return child
            except OSError:
                if child.poll() is not None or time.monotonic() >= deadline:
                    import signal
                    if child.poll() is None:
                        child.send_signal(signal.SIGINT); child.wait(timeout=20)
                    raise RuntimeError(f"Verification backend failed; inspect {log.name}")
                time.sleep(0.25)

    def stop(child):
        if child and child.poll() is None:
            # SIGINT lets the launcher terminate only its own backend subprocess.
            import signal
            child.send_signal(signal.SIGINT); child.wait(timeout=20)

    result = {"synthetic_only": True, "cloud_calls": 0, "validation_directory": str(root)}
    try:
        process = start()
        documents = []
        for name, text in (
            ("synthetic-a.md", "完全合成资料。制造试验站 SYNTH_MFG_001 的供电功率上限是12千瓦。此参数只用于软件验证，不对应真实设备。"),
            ("synthetic-b.md", "完全合成资料。对照试验站 SYNTH_MFG_002 的供电功率上限是18千瓦。它与 SYNTH_MFG_001 不同。"),
        ):
            upload = call(f"/api/documents/upload?filename={name}&domain=synthetic_manufacturing", raw=text.encode())
            document = upload["document"]
            processed = call(f"/api/documents/{document['document_id']}/process", {})
            documents.append(processed["document"])
        options = {"runtime_mode":"configured", "metadata_router":False, "query_transformation":False,
                   "metadata_filter":{"domain":"synthetic_manufacturing"}, "query_rewrite":False,
                   "query_expansion":False, "compression":False, "parent_recovery":False, "top_k":2,"final_top_k":1}
        query = {"question":"SYNTH_MFG_001 的供电功率上限是多少千瓦？请引用 source ID。", "options":options}
        first = call("/api/v2/query/test", query)
        result["first_trace"] = first
        assert first["runtime"]["mode"] == "configured"
        assert first["runtime"]["generation_model"] == "qwen3.5-9b-mlx-4bit"
        assert {c["stage"] for c in first["runtime"]["calls"]} == {"embedding","rerank","chat"}
        assert "12" in first["answer"] and first["reranked_docs"][0]["doc_id"] == documents[0]["document_id"]
        assert first["reranked_docs"][0]["chunk_id"] in first["answer"], "Answer lacks its actual citation"
        assert first["runtime"]["embedding_dimension"] == 512
        before = call("/api/state")
        dataset = call("/api/v1/evaluation/datasets", {"dataset_id":"synthetic_local_p1", "samples":[{
            "query_id":"synthetic-1", "query":query["question"], "category":"synthetic_manufacturing",
            "gold_doc_ids":[documents[0]["document_id"]], "ground_truth":"供电功率上限是12千瓦。"}]})
        report = call("/api/v1/evaluation/run", {"eval_set":dataset["eval_set"], "experiment_name":"local_p1_synthetic", "options":options})
        result["evaluation_run"] = report
        stop(process); process = start()
        after = call("/api/state")
        assert [c["chunk_id"] for c in before["chunks"]] == [c["chunk_id"] for c in after["chunks"]]
        assert call("/api/v2/traces/"+first["trace_id"])["answer"] == first["answer"]
        assert call("/api/v1/evaluation/runs/"+report["run_id"])["run_id"] == report["run_id"]
        recovered = call("/api/v2/query/test", query)
        assert not any(c["stage"] == "embedding" for c in recovered["runtime"]["calls"]), "Persisted vector cache was not reused"
        assert recovered["runtime"]["vector_cache_hits"] >= 3
        no_evidence = call("/api/v2/query/test", {**query, "options":{**options, "metadata_filter":{"domain":"absent_synthetic_domain"}}})
        assert not no_evidence["reranked_docs"] and not any(c["stage"] == "chat" for c in no_evidence["runtime"]["calls"])
        call("/api/documents/"+documents[0]["document_id"], method="DELETE")
        assert Path(documents[0]["local_path"]).is_file()
        assert not any(c["document_id"] == documents[0]["document_id"] for c in call("/api/chunks")["items"])
        result.update(status="passed", first_trace=first, restarted_trace=recovered,
                      evaluation_run=report, assertions=["real_embedding_rerank_mlx_answer", "correct_answer_and_citation", "restart_document_trace_report_recovery", "persisted_vector_cache", "explicit_filter_no_evidence", "logical_delete_retains_original"])
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        stop(process); log.close()
        (root/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status":result.get("status"),"evidence":str(root/"result.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
