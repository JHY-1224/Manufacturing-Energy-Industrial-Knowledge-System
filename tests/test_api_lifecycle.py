import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.engine import ConfigurableRagEngine
from app.knowledge.service import KnowledgeService
from app.storage.knowledge_store import KnowledgeStore


class ApiLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="p1-api-"))
        self.store = KnowledgeStore(self.root/"runtime")
        self.store.set_meta("initialized", "1")
        self.engine = ConfigurableRagEngine(self.store)
        from app.main import app
        from app.api import dependencies, routes_ingest, routes_query, routes_graph_retrieval
        from app.graph_retrieval.graph_store import GraphStore
        for target, name, value in (
            (dependencies, "engine", self.engine), (dependencies, "service", KnowledgeService(self.engine)),
            (routes_ingest, "engine", self.engine), (routes_query, "engine", self.engine),
            (routes_graph_retrieval, "engine", self.engine),
            (routes_graph_retrieval, "graph_store", GraphStore(self.root/"graph.json")),
            (routes_graph_retrieval, "_source_signature", None),
        ):
            mock = patch.object(target, name, value); mock.start(); self.addCleanup(mock.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self):
        response = self.client.post("/api/documents/upload?filename=synthetic.md&domain=wind_oam", content="AccY 对应塔架共振的合成检查。测试参数为 12 千瓦。".encode())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["document"]

    def test_legacy_upload_v2_ingest_query_delete_share_one_lifecycle(self):
        document = self.upload(); doc_id = document["document_id"]
        processed = self.client.post("/api/v2/ingest", json={"path": document["local_path"], "domain": "wind_oam"})
        self.assertEqual(processed.status_code, 200, processed.text)
        self.assertEqual(processed.json()["document_id"], doc_id)
        state = self.client.get("/api/state").json()
        self.assertEqual(len(state["documents"]), 1)
        self.assertEqual(state["documents"][0]["chunk_count"], len(state["chunks"]))
        response = self.client.post("/api/v2/query/test", json={"question": "测试参数多少千瓦？"})
        self.assertEqual(response.status_code, 200, response.text)
        trace = response.json()
        self.assertIn("12", trace["answer"])
        self.assertEqual({hit["doc_id"] for hit in trace["reranked_docs"]}, {doc_id})
        self.assertEqual(self.client.get("/api/v2/traces/"+trace["trace_id"]).status_code, 200)
        self.assertEqual(self.client.delete("/api/documents/"+doc_id).status_code, 200)
        self.assertEqual(self.client.get("/api/chunks").json()["items"], [])
        self.assertEqual(self.client.get("/api/state").json()["documents"], [])
        self.assertTrue(Path(document["local_path"]).is_file())

    def test_legacy_process_retrieval_and_graph_follow_deletion(self):
        doc_id = self.upload()["document_id"]
        result = self.client.post(f"/api/documents/{doc_id}/process", json={})
        self.assertEqual(result.status_code, 200, result.text)
        original_ids = {chunk["chunk_id"] for chunk in result.json()["chunks"]}
        duplicate = self.client.post(f"/api/documents/{doc_id}/process", json={})
        self.assertTrue(duplicate.json()["ingestion"]["reused"])
        retrieval = self.client.post("/api/retrieval/search", json={"query": "AccY", "domain": "wind_oam"})
        self.assertEqual(retrieval.status_code, 200, retrieval.text)
        self.assertTrue({item["chunk_id"] for item in retrieval.json()["items"]}.issubset(original_ids))
        self.assertTrue(self.client.get("/api/v2/graph/entities").json()["items"])
        self.client.delete("/api/documents/"+doc_id)
        self.assertEqual(self.client.get("/api/v2/graph/entities").json()["items"], [])
        self.assertEqual(self.client.post("/api/retrieval/search", json={"query":"AccY"}).json()["items"], [])

    def test_invalid_modes_and_missing_real_configuration_are_not_successes(self):
        self.assertEqual(self.client.post("/api/retrieval/search", json={"query":"test","mode":"unknown"}).status_code, 422)
        self.assertEqual(self.client.get("/api/retrieval?mode=unknown").status_code, 422)
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/v2/query/test", json={"question":"合成问题", "options":{"runtime_mode":"configured"}})
        self.assertEqual(response.status_code, 503)
        trace = self.client.get("/api/v2/traces/"+response.json()["trace_id"]).json()
        self.assertEqual(trace["runtime"]["status"], "failed")
        self.assertEqual(trace["answer"], "")
