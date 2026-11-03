from __future__ import annotations

import hashlib
import math
import re


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_./-]+|[\u4e00-\u9fff]", text.lower())


class HashEmbedding:
    def __init__(self, model_name: str, dimension: int = 256) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self.fingerprint = hashlib.sha256(f"offline-hash-sha256-v1:{dimension}".encode()).hexdigest()
        self.last_call = None

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class EmbeddingFactory:
    SUPPORTED = ["huggingface-local", "bge-large-zh", "qwen-embedding", "openai", "dashscope"]

    @classmethod
    def create(cls, model_name: str = "bge-large-zh", dimension: int = 256):
        return HashEmbedding(model_name=model_name, dimension=dimension)
