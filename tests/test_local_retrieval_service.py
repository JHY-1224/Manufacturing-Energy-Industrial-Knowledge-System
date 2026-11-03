import unittest
from fastapi.testclient import TestClient
from scripts.serve_local_retrieval import build_app, EMBEDDING_MODEL, RERANK_MODEL


class FixtureRuntime:
    def embed(self, texts):
        if texts[0] == "over-segment-limit":
            raise ValueError("Request exceeds 128 model segments")
        return [[1.0] + [0.0] * 511 for _ in texts], len(texts) * 3, {"strategy": "token_chunks_weighted_mean_l2_v1", "segments_per_input": [1] * len(texts), "truncated": False}

    def rerank(self, query, documents):
        return [0.1, 0.9, 0.5][:len(documents)], len(documents) * 6, {"strategy": "document_token_chunks_max_v1", "segments_per_input": [1] * len(documents), "truncated": False}


class LocalRetrievalServiceTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app(FixtureRuntime()))
        self.addCleanup(self.client.close)

    def test_embedding_contract_and_model_identity(self):
        result = self.client.post("/v1/embeddings", json={"model": EMBEDDING_MODEL, "input": ["合成工艺", "合成能源"], "dimensions": 512})
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["model"], EMBEDDING_MODEL)
        self.assertEqual([row["index"] for row in payload["data"]], [0, 1])
        self.assertEqual(len(payload["data"][0]["embedding"]), 512)
        self.assertEqual(payload["usage"]["total_tokens"], 6)
        self.assertFalse(payload["processing"]["truncated"])
        self.assertEqual(self.client.post("/v1/embeddings", json={"model": "remote/model", "input": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/v1/embeddings", json={"model": EMBEDDING_MODEL, "input": "x", "dimensions": 128}).status_code, 400)

    def test_embedding_rejects_empty_or_over_budget_input(self):
        for texts in ([], [""], ["x"] * 33, "over-segment-limit"):
            self.assertEqual(self.client.post("/v1/embeddings", json={"model": EMBEDDING_MODEL, "input": texts}).status_code, 422)

    def test_rerank_native_contract_sorting_and_document_echo(self):
        path = "/api/v1/services/rerank/text-rerank/text-rerank"
        body = {"model": RERANK_MODEL, "input": {"query": "合成问题", "documents": ["无关", "相关", "部分相关"]}, "parameters": {"top_n": 2}}
        result = self.client.post(path, json=body)
        self.assertEqual(result.status_code, 200)
        rows = result.json()["output"]["results"]
        self.assertEqual([row["index"] for row in rows], [1, 2])
        self.assertNotIn("document", rows[0])
        body["parameters"]["return_documents"] = True
        self.assertEqual(self.client.post(path, json=body).json()["output"]["results"][0]["document"]["text"], "相关")
        body["model"] = "another-model"
        self.assertEqual(self.client.post(path, json=body).status_code, 400)


if __name__ == "__main__":
    unittest.main()
