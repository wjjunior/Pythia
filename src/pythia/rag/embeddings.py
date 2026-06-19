"""Embedding adapters (§4.5, §3.9): a deterministic stub and an OpenAI-compatible provider.

The stub is a hashing vectorizer — deterministic, zero-secret, and carrying enough lexical
signal that retrieval ranking is testable offline. With no embedding provider configured the
whole RAG path runs on the stub, so CI never needs a key.
"""

import hashlib
import math
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from pythia.common import EmbeddingPort
from pythia.config import Settings
from pythia.db.models import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


class EmbeddingResponseError(RuntimeError):
    """The embedding provider returned a response that could not be parsed."""


class _EmbeddingItem(BaseModel):
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    data: list[_EmbeddingItem]


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class StubEmbeddingPort:
    """Deterministic hashing vectorizer: tokens hash into buckets, then L2-normalize."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode()).digest()
            bucket = int.from_bytes(digest[:8], "big") % self._dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class EmbeddingTransport(Protocol):
    def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


class HttpxEmbeddingTransport:
    """Live transport for the OpenAI-compatible /embeddings endpoint."""

    def __init__(self, api_key: str, base_url: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        response = self._client.post("/embeddings", json={"model": model, "input": texts})
        response.raise_for_status()
        try:
            parsed = _EmbeddingResponse.model_validate_json(response.text)
        except ValidationError as exc:
            raise EmbeddingResponseError(f"invalid embeddings response: {exc}") from exc
        return [item.embedding for item in parsed.data]


class ProviderEmbeddingPort:
    """EmbeddingPort backed by a real embeddings provider, via an injected transport."""

    def __init__(self, transport: EmbeddingTransport, model: str) -> None:
        self._transport = transport
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._transport.embed(model=self._model, texts=texts)


def make_embedder(settings: Settings) -> EmbeddingPort:
    if settings.embedding_provider and settings.embedding_api_key and settings.embedding_model:
        base_url = settings.embedding_base_url or settings.openrouter_base_url
        transport = HttpxEmbeddingTransport(settings.embedding_api_key, base_url)
        return ProviderEmbeddingPort(transport, settings.embedding_model)
    return StubEmbeddingPort()
