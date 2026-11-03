"""Loopback-only BGE embeddings and reranking using pinned, local safetensors.

Models are downloaded separately with download_local_retrieval.py. This service
has no upload, training, model download, or arbitrary model/path selection API.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import threading
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
RERANK_MODEL = "BAAI/bge-reranker-base"
RERANK_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
EMBEDDING_DIMENSIONS = 512
MAX_TOKENS = 512
MAX_BATCH = 32
MAX_SEGMENTS = 128
EMBEDDING_STRATEGY = "token_chunks_weighted_mean_l2_v1"
RERANK_STRATEGY = "document_token_chunks_max_v1"


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    input: str | list[str]
    encoding_format: Literal["float"] = "float"
    dimensions: int | None = Field(default=None, strict=True, ge=1)


class RerankInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    documents: list[str] = Field(min_length=1, max_length=MAX_BATCH)


class RerankParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_n: int = Field(default=5, strict=True, ge=1, le=MAX_BATCH)
    return_documents: bool = False


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    input: RerankInput
    parameters: RerankParameters = Field(default_factory=RerankParameters)


def validated_texts(value) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_BATCH:
        raise ValueError("Input must contain 1-32 text strings")
    if any(not isinstance(text, str) or not text.strip() or len(text) > 16000 or "\x00" in text for text in values):
        raise ValueError("Input texts must be non-empty and at most 16000 characters")
    return values


class LocalRetrieval:
    def __init__(self, models_root: Path, threads: int = 4):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import torch
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        torch.set_num_threads(threads)
        self.lock = threading.Lock()
        embedding_path = models_root / EMBEDDING_MODEL.replace("/", "--") / EMBEDDING_REVISION
        rerank_path = models_root / RERANK_MODEL.replace("/", "--") / RERANK_REVISION
        for directory in (embedding_path, rerank_path):
            if not (directory / "model.safetensors").is_file():
                raise ValueError("Pinned local safetensors missing; run download_local_retrieval.py first")
        kwargs = {"local_files_only": True, "trust_remote_code": False}
        self.embedding_tokenizer = AutoTokenizer.from_pretrained(str(embedding_path), use_fast=True, **kwargs)
        self.embedding_model = AutoModel.from_pretrained(str(embedding_path), use_safetensors=True, torch_dtype=torch.float32, **kwargs).to("cpu").eval()
        self.rerank_tokenizer = AutoTokenizer.from_pretrained(str(rerank_path), use_fast=True, **kwargs)
        self.rerank_model = AutoModelForSequenceClassification.from_pretrained(str(rerank_path), use_safetensors=True, torch_dtype=torch.float32, **kwargs).to("cpu").eval()
        if self.embedding_model.config.hidden_size != EMBEDDING_DIMENSIONS:
            raise ValueError("Embedding model dimension does not match the pinned contract")

    def embed(self, texts: list[str]):
        with self.lock, self.torch.inference_mode():
            tokenizer = self.embedding_tokenizer
            document_ids = tokenizer(texts, add_special_tokens=False, truncation=False)["input_ids"]
            capacity = MAX_TOKENS - tokenizer.num_special_tokens_to_add(pair=False)
            features, owners, lengths, counts = [], [], [], []
            for owner, ids in enumerate(document_ids):
                if not ids:
                    raise ValueError("Input has no usable model tokens")
                counts.append((len(ids) + capacity - 1) // capacity)
                for start in range(0, len(ids), capacity):
                    part = ids[start:start + capacity]
                    features.append(tokenizer.prepare_for_model(part, add_special_tokens=True, truncation=False, return_attention_mask=True))
                    owners.append(owner)
                    lengths.append(len(part))
            if len(features) > MAX_SEGMENTS:
                raise ValueError("Request exceeds 128 model segments; send a smaller batch")
            sums = self.torch.zeros((len(texts), EMBEDDING_DIMENSIONS))
            token_count = 0
            for offset in range(0, len(features), 8):
                encoded = tokenizer.pad(features[offset:offset + 8], padding=True, return_tensors="pt")
                # Official BGE recipe per segment: CLS pooling and L2 normalization.
                vectors = self.embedding_model(**encoded).last_hidden_state[:, 0]
                vectors = self.torch.nn.functional.normalize(vectors, p=2, dim=1)
                for index, vector in enumerate(vectors, start=offset):
                    sums[owners[index]] += vector * lengths[index]
                token_count += int(encoded["attention_mask"].sum().item())
            embeddings = self.torch.nn.functional.normalize(sums, p=2, dim=1)
            if not self.torch.isfinite(embeddings).all() or (embeddings.norm(dim=1) <= 0).any():
                raise ValueError("Model returned invalid embeddings")
            return embeddings.tolist(), token_count, {"strategy": EMBEDDING_STRATEGY, "segments_per_input": counts, "source_tokens_per_input": [len(ids) for ids in document_ids], "max_model_tokens": MAX_TOKENS, "segment_token_limit": capacity, "truncated": False}

    def rerank(self, query: str, documents: list[str]):
        with self.lock, self.torch.inference_mode():
            tokenizer = self.rerank_tokenizer
            query_ids = tokenizer(query, add_special_tokens=False, truncation=False)["input_ids"]
            if not query_ids or len(query_ids) > 256:
                raise ValueError("Rerank query must contain 1-256 model tokens; no truncation is applied")
            document_ids = tokenizer(documents, add_special_tokens=False, truncation=False)["input_ids"]
            capacity = MAX_TOKENS - len(query_ids) - tokenizer.num_special_tokens_to_add(pair=True)
            features, owners, counts, boundaries = [], [], [], []
            for owner, ids in enumerate(document_ids):
                if not ids:
                    raise ValueError("Document has no usable model tokens")
                counts.append((len(ids) + capacity - 1) // capacity)
                for start in range(0, len(ids), capacity):
                    part = ids[start:start + capacity]
                    features.append(tokenizer.prepare_for_model(query_ids, pair_ids=part, add_special_tokens=True, truncation=False, return_attention_mask=True))
                    owners.append(owner)
                    boundaries.append((start, start + len(part)))
            if len(features) > MAX_SEGMENTS:
                raise ValueError("Request exceeds 128 model segments; send a smaller batch")
            scores, matches, token_count = [-1.0] * len(documents), [None] * len(documents), 0
            for offset in range(0, len(features), 8):
                encoded = tokenizer.pad(features[offset:offset + 8], padding=True, return_tensors="pt")
                logits = self.rerank_model(**encoded).logits.reshape(-1)
                if logits.shape[0] != len(features[offset:offset + 8]) or not self.torch.isfinite(logits).all():
                    raise ValueError("Model returned invalid reranking scores")
                for index, score in enumerate(self.torch.sigmoid(logits).tolist(), start=offset):
                    owner = owners[index]
                    if score > scores[owner]:
                        scores[owner] = score
                        matches[owner] = {"start_token": boundaries[index][0], "end_token": boundaries[index][1]}
                token_count += int(encoded["attention_mask"].sum().item())
            return scores, token_count, {"strategy": RERANK_STRATEGY, "segments_per_input": counts, "matched_segments": matches, "source_tokens_per_input": [len(ids) for ids in document_ids], "max_model_tokens": MAX_TOKENS, "query_tokens": len(query_ids), "segment_token_limit": capacity, "truncated": False}


def build_app(runtime) -> FastAPI:
    app = FastAPI(title="制造能源工业知识库系统 · 本地检索模型", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "device": "cpu", "offline_models": True,
                "embedding_model": EMBEDDING_MODEL, "embedding_revision": EMBEDDING_REVISION,
                "embedding_dimensions": EMBEDDING_DIMENSIONS, "reranker_model": RERANK_MODEL,
                "reranker_revision": RERANK_REVISION, "max_tokens": MAX_TOKENS,
                "truncation": "none", "embedding_strategy": EMBEDDING_STRATEGY, "rerank_strategy": RERANK_STRATEGY,
                "max_segments": MAX_SEGMENTS, "max_batch": MAX_BATCH}

    @app.post("/v1/embeddings")
    def embeddings(payload: EmbeddingRequest):
        if payload.model != EMBEDDING_MODEL or payload.dimensions not in (None, EMBEDDING_DIMENSIONS):
            raise HTTPException(status_code=400, detail="Requested model/dimensions do not match the loaded embedding model")
        try:
            vectors, tokens, processing = runtime.embed(validated_texts(payload.input))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"object": "list", "model": EMBEDDING_MODEL,
                "data": [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)],
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens}, "processing": processing}

    @app.post("/api/v1/services/rerank/text-rerank/text-rerank")
    def rerank(payload: RerankRequest):
        if payload.model != RERANK_MODEL:
            raise HTTPException(status_code=400, detail="Requested model does not match the loaded reranker")
        try:
            query = validated_texts(payload.input.query)[0]
            documents = validated_texts(payload.input.documents)
            scores, tokens, processing = runtime.rerank(query, documents)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        indexes = sorted(range(len(scores)), key=lambda index: (-scores[index], index))[:payload.parameters.top_n]
        results = []
        for index in indexes:
            result = {"index": index, "relevance_score": scores[index]}
            if payload.parameters.return_documents:
                result["document"] = {"text": documents[index]}
            results.append(result)
        return {"request_id": uuid4().hex, "model": RERANK_MODEL, "output": {"results": results},
                "usage": {"input_tokens": tokens, "total_tokens": tokens}, "processing": processing}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, default=Path(__file__).resolve().parents[1] / "data/runtime/models")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--threads", type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn
    runtime = LocalRetrieval(args.models_root.resolve(), args.threads)
    uvicorn.run(build_app(runtime), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
