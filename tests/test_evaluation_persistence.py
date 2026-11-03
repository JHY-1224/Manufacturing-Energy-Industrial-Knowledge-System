import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.models import EvalCase, QueryOptions, QueryTrace, RagChunk, RetrievalHit
from app.evaluation.eval_dataset import EvalDataset
from app.evaluation.experiment_runner import ExperimentRunner, options_for_experiment
from app.evaluation.ragas_diagnostics import build_evaluation_summary
from app.evaluation.report_generator import ReportGenerator


class OfflineFixtureEngine:
    """Synthetic retrieval fixture; never loads a model or writes a trace."""

    def __init__(self):
        self.chunks = [
            RagChunk(doc_id="document-A", chunk_id="fragment-A", content="alpha synthetic evidence"),
            RagChunk(doc_id="chunk_named_document", chunk_id="fragment-B", content="beta evidence"),
        ]
        self.options = []

    def query(self, request):
        self.options.append(request.options.model_dump())
        chunk = self.chunks[0]
        return QueryTrace(
            original_query=request.question, rewritten_query=request.question,
            retrieval_mode=request.options.retrieval_mode, options=request.options.model_dump(),
            reranked_docs=[RetrievalHit(**chunk.model_dump(exclude={"parent_id", "parent_content"}), score=1)],
            compressed_context=[chunk.content], answer="alpha synthetic evidence",
            latency_ms={"total": 1}, token_usage={"input_tokens": 3, "output_tokens": 3},
        )


class EvaluationPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="p1-evaluation-test-"))
        self.datasets = self.root / "datasets"
        self.reports = self.root / "reports"
        self.configs = self.root / "configs"
        self.datasets.mkdir()
        self.configs.mkdir()
        self.env = patch.dict(os.environ, {"RAG_DATA_DIR": str(self.root / "runtime"), "RAG_EVAL_DIR": str(self.datasets), "RAG_REPORT_DIR": str(self.reports)})
        self.env.start()
        self.addCleanup(self.env.stop)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import routes_eval, routes_experiment
        self.routes = routes_eval
        self.fixture = OfflineFixtureEngine()
        for target, name, value in (
            (routes_eval, "EVAL_DIR", self.datasets), (routes_eval, "REPORT_DIR", self.reports),
            (routes_eval, "engine", self.fixture), (routes_experiment, "engine", self.fixture),
            (routes_experiment, "CONFIG_DIR", self.configs),
        ):
            mock = patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        app = FastAPI()
        app.include_router(routes_eval.router)
        app.include_router(routes_eval.evaluation_router)
        app.include_router(routes_experiment.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create_dataset(self, name="synthetic", references=None):
        response = self.client.post("/api/v1/evaluation/datasets", json={
            "dataset_id": name,
            "samples": [{"eval_id": "case-A", "question": "alpha", "domain": "synthetic",
                         "reference_context_ids": references if references is not None else ["fragment-A"],
                         "ground_truth": "alpha synthetic evidence"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["eval_set"]

    def test_empty_summary_is_unknown_and_demo_is_explicit(self):
        summary = self.client.get("/api/v1/evaluation/metrics/summary").json()
        for key in ("runs", "sample_results", "failures", "trend", "config_comparisons", "datasets"):
            self.assertEqual(summary[key], [])
        self.assertTrue(all(card["current_avg"] is None and card["previous_avg"] is None for card in summary["metric_cards"]))
        self.assertIsNone(summary["automatic_diagnosis"]["primary_failure_type"])
        self.assertEqual(summary["automatic_diagnosis"]["status"], "insufficient_data")
        demo = self.client.get("/api/v1/evaluation/metrics/summary?demo=true").json()
        self.assertEqual(len(demo["runs"]), 2)
        self.assertTrue(all(run["data_source"] == "demo" for run in demo["runs"]))
        self.assertEqual(len({run["run_id"] for run in demo["runs"]}), 2)
        self.assertEqual(self.client.get("/api/v1/evaluation/runs/missing/samples").status_code, 404)

    def test_created_and_uploaded_datasets_are_visible_and_cannot_be_overwritten(self):
        path = Path(self.create_dataset(name="制造样例"))
        original = path.read_bytes()
        listed = self.client.get("/api/v1/evaluation/datasets").json()["items"]
        self.assertEqual([item["dataset_id"] for item in listed], ["制造样例"])
        self.assertEqual(listed[0]["sample_count"], 1)
        details = self.client.get("/api/v1/evaluation/datasets/制造样例").json()
        self.assertEqual(details["samples"][0]["query_id"], "case-A")
        self.assertEqual(details["samples"][0]["gold_doc_ids"], ["fragment-A"])
        duplicate = self.client.post("/api/v2/eval/upload", params={"filename": path.name}, content=original)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(path.read_bytes(), original)
        uploaded = self.client.post("/api/v2/eval/upload?filename=another.jsonl", content=original)
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(len(self.client.get("/api/v1/evaluation/datasets").json()["items"]), 2)

    def test_bad_inputs_are_rejected_without_creating_files(self):
        for content in (b"[]", b"null", b"1", b"{broken", b'{"query_id":"x","query":" ","category":"a"}', b'{"query_id":"x","query":"a","category":"a","relevance":{"document-A":NaN}}'):
            with self.subTest(content=content):
                self.assertEqual(self.client.post("/api/v2/eval/upload?filename=bad.jsonl", content=content).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/evaluation/datasets", json={"dataset_id": "bad", "samples": [None]}).status_code, 422)
        self.assertEqual(self.client.post("/api/v2/eval/upload", params={"filename": "../outside.jsonl"}, content=b"{}").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/evaluation/run", json={"eval_set": str(self.root / "outside.jsonl")}).status_code, 400)
        self.assertEqual(list(self.datasets.iterdir()), [])
        self.assertEqual(self.client.post("/api/v1/evaluation/diagnose", json={"faithfulness": "invalid"}).status_code, 422)
        (self.datasets / "corrupt.jsonl").write_text("[]", encoding="utf-8")
        self.assertEqual(self.client.get("/api/v1/evaluation/datasets").json()["items"][0]["status"], "invalid")
        self.assertEqual(self.client.get("/api/v1/evaluation/datasets/corrupt").status_code, 422)

    def test_runs_have_unique_complete_files_and_summary_uses_actual_samples(self):
        dataset = self.create_dataset(references=["fragment-B"])
        responses = [self.client.post("/api/v1/evaluation/run", json={"eval_set": dataset, "experiment_name": "same-name"}) for _ in range(2)]
        self.assertTrue(all(item.status_code == 200 for item in responses), [item.text for item in responses])
        runs = [item.json() for item in responses]
        self.assertNotEqual(runs[0]["run_id"], runs[1]["run_id"])
        self.assertEqual(len(list(self.reports.glob("*.json"))), 2)
        for run in runs:
            stored = json.loads(Path(run["report_files"]["json"]).read_text())
            self.assertEqual(stored, run)
            self.assertEqual(stored["eval_dataset"], dataset)
            self.assertEqual(stored["evaluation_mode"], "offline_proxy")
            self.assertEqual(stored["sample_results"][0]["reference_id_kind"], "chunk")
            self.assertEqual(stored["sample_results"][0]["retrieval_metrics"]["hit_at_5"], 0)
            self.assertEqual(stored["sample_results"][0]["failure_type"], "retrieval_miss")
            self.assertEqual(self.client.get(f"/api/v1/evaluation/runs/{run['run_id']}/samples").json()["items"], run["sample_results"])
            self.assertEqual(self.client.get(f"/api/v2/eval/reports/{Path(run['report_files']['json']).name}").status_code, 200)
        summary = self.client.get("/api/v1/evaluation/metrics/summary").json()
        self.assertEqual(len(summary["runs"]), 2)
        self.assertEqual(len(summary["sample_results"]), 2)
        self.assertEqual(len(summary["failures"]), 2)
        self.assertEqual(len(summary["trend"]), 2)
        self.assertEqual(len(summary["config_comparisons"]), 2)
        self.assertEqual(summary["metric_cards"][0]["evaluation_mode"], "offline_proxy")
        self.assertEqual({item["run_id"] for item in summary["failures"]}, {run["run_id"] for run in runs})
        self.assertTrue(all(item["eval_id"] == "case-A" for item in summary["sample_results"]))

    def test_reference_kind_uses_actual_ids_and_rejects_ambiguous_mixed_grain(self):
        runner = ExperimentRunner(self.fixture, self.reports)
        for identifier, kind, expected in (("fragment-A", "chunk", 1), ("document-A", "document", 1), ("chunk_named_document", "document", 0)):
            case = EvalCase(query_id="one", query="alpha", category="test", gold_doc_ids=[identifier])
            report = runner.run("references", [case], QueryOptions(), write_report=False)
            self.assertEqual(report["sample_results"][0]["reference_id_kind"], kind)
            self.assertEqual(report["metrics"]["hit_at_5"], expected)
        case = EvalCase(query_id="mixed", query="alpha", category="test", gold_doc_ids=["document-A", "fragment-B"])
        with self.assertRaisesRegex(ValueError, "mix"):
            runner.run("mixed", [case], QueryOptions())
        self.assertFalse(self.reports.exists())

    def test_reference_kind_refreshes_another_workers_new_document(self):
        new_chunk = RagChunk(doc_id="another-worker-document", chunk_id="new-fragment-without-prefix", content="alpha synthetic evidence")
        calls = []
        def refresh():
            calls.append(True)
            self.fixture.chunks = [new_chunk]
        self.fixture.refresh = refresh
        case = EvalCase(query_id="late-upload", query="alpha", category="test", gold_doc_ids=[new_chunk.chunk_id])
        report = ExperimentRunner(self.fixture, self.reports).run("fresh-corpus", [case], QueryOptions(), write_report=False)
        self.assertEqual(calls, [True])
        self.assertEqual(report["sample_results"][0]["reference_id_kind"], "chunk")
        self.assertEqual(report["metrics"]["hit_at_5"], 1.0)

    def test_reports_are_exclusive_and_experiment_names_are_not_paths(self):
        generator = ReportGenerator(self.reports)
        report = {"run_id": "fixed_id", "metrics": {}, "eval_dataset": "synthetic", "sample_results": []}
        paths = generator.write("display-name", report)
        original = Path(paths["json"]).read_bytes()
        with self.assertRaises(FileExistsError):
            generator.write("same", dict(report, metrics={"changed": 1}))
        self.assertEqual(Path(paths["json"]).read_bytes(), original)
        with self.assertRaises(ValueError):
            generator.write("../outside", {"metrics": {}})
        with self.assertRaises(ValueError):
            generator.write("safe", {"run_id": "../escape", "metrics": {}})
        comparison = generator.write_comparison([{"experiment": "no-labels", "metrics": {"faithfulness": 1}}])
        self.assertIsNone(comparison["experiments"][0]["quality_score"])
        self.assertIsNone(comparison["recommended_experiment"])
        self.assertEqual(json.loads(Path(comparison["report_files"]["json"]).read_text()), comparison)

    def test_presets_are_independent_and_persist_correct_dataset(self):
        first = options_for_experiment({"name": "first", "metadata_filter": {"domain": "first"}, "rag_fusion": True})
        second = options_for_experiment({"name": "second", "retrieval_mode": "vector"})
        self.assertEqual(second.metadata_filter, {})
        self.assertFalse(second.rag_fusion)
        self.assertFalse(first.query_transformation)
        self.assertFalse(second.query_transformation)
        dataset = self.create_dataset()
        config = self.configs / "experiments.json"
        config.write_text(json.dumps({"experiments": [{"name": "one", "rag_fusion": True}, {"name": "two", "retrieval_mode": "vector"}]}))
        response = self.client.post("/api/v2/experiments/run", params={"config_path": str(config), "eval_set": dataset})
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["experiments"]
        self.assertEqual([item["eval_dataset"] for item in results], [dataset, dataset])
        self.assertEqual([item["options"]["rag_fusion"] for item in results], [True, False])
        self.assertTrue(all(not item["options"]["query_transformation"] for item in results))
        config.write_text('{"experiments":[null]}')
        self.assertEqual(self.client.post("/api/v2/experiments/run", params={"config_path": str(config), "eval_set": dataset}).status_code, 422)

    def test_corrupt_report_files_do_not_hide_valid_runs(self):
        self.reports.mkdir()
        for name, content in (("array", "[]"), ("broken", "{"), ("wrong", '{"run_id":"bad","metrics":[]}'), ("nan", '{"run_id":"nan","metrics":{"faithfulness":NaN}}')):
            (self.reports / f"{name}.json").write_text(content)
        self.assertEqual(self.client.get("/api/v1/evaluation/runs").json()["items"], [])
        with self.assertRaises(ValueError):
            EvalDataset.loads('{"query_id":"x","query":"a","category":"test"}\n' * 2)
        zero = build_evaluation_summary([{"run_id": "real-zero", "avg_faithfulness": 0.0}])
        self.assertEqual(zero["averages"]["faithfulness"], 0.0)
        self.assertIsNone(zero["averages"]["context_recall"])


if __name__ == "__main__":
    unittest.main()
