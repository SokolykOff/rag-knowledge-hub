"""EmbeddingProvider: OpenAI-compatible API when a key is set, otherwise a free deterministic local model."""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Protocol

from app.core.resilience import CircuitBreaker, TransientError, request_with_retries
from app.retrieval.text import _STOP, stem, tokenize

DIM = 384


class ProviderError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % dim, 1.0 if (h >> 40) & 1 else -1.0


def local_embedding(text: str, dim: int = DIM) -> list[float]:
    """Deterministic hashed bag of stems + stem bigrams + char trigrams, L2-normalised."""
    toks = [t for t in tokenize(text) if t not in _STOP and len(t) > 1]
    stems = [stem(t) for t in toks]
    feats: Counter[str] = Counter()
    for s in stems:
        feats[f"w:{s}"] += 1.0
    for a, b in zip(stems, stems[1:]):
        feats[f"b:{a}_{b}"] += 0.4
    for t in toks:
        padded = f"^{t}$"
        for i in range(len(padded) - 2):
            feats[f"c:{padded[i:i + 3]}"] += 0.15
    vec = [0.0] * dim
    for f, c in feats.items():
        idx, sign = _bucket(f, dim)
        vec[idx] += sign * (1.0 + math.log(c) if c >= 1 else c)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class LocalHashEmbedder:
    name = "local-hash-v1"

    def __init__(self, dim: int = DIM):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [local_embedding(t, self.dim) for t in texts]


class OpenAICompatEmbedder:
    """Calls `{base_url}/embeddings`. No silent fallback to the local model: vectors from different
    models are not comparable, so a failure raises ProviderError (the ingest worker retries -> DLQ)."""

    def __init__(self, api_key: str, base_url: str, model: str, dim: int = DIM, transport=None):
        self.api_key, self.base_url, self.model, self.dim = api_key, base_url.rstrip("/"), model, dim
        self.name = f"openai:{model}:{dim}"
        self._transport = transport
        self._breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            for i in range(0, len(texts), 64):
                batch = texts[i : i + 64]

                async def send(batch=batch):
                    try:
                        r = await client.post(
                            f"{self.base_url}/embeddings",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            json={"model": self.model, "input": batch, "dimensions": self.dim},
                        )
                    except httpx.TransportError as exc:
                        raise TransientError(str(exc)) from exc
                    return r.status_code, dict(r.headers), (r.json() if r.content else {})

                try:
                    body = await request_with_retries(send, breaker=self._breaker)
                except Exception as exc:  # noqa: BLE001
                    raise ProviderError(f"embedding provider failed: {exc}") from exc
                vectors = [d["embedding"] for d in sorted(body["data"], key=lambda d: d["index"])]
                if any(len(v) != self.dim for v in vectors):
                    raise ProviderError(f"provider returned dim != {self.dim}; use a model supporting `dimensions`")
                out.extend(vectors)
        return out


def build_embedder(settings) -> EmbeddingProvider:
    if settings.embedding_api_key:
        return OpenAICompatEmbedder(settings.embedding_api_key, settings.embedding_base_url, settings.embedding_model, settings.embedding_dim)
    return LocalHashEmbedder(settings.embedding_dim)
