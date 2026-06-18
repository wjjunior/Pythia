"""Tests for the deterministic stub embedder and the embedder factory."""

import math

from pythia.config import Settings
from pythia.rag import ProviderEmbeddingPort, StubEmbeddingPort, make_embedder


def test_stub_embedding_is_deterministic() -> None:
    embedder = StubEmbeddingPort()
    assert embedder.embed(["hello world"]) == embedder.embed(["hello world"])


def test_stub_embedding_has_the_configured_dimension() -> None:
    [vector] = StubEmbeddingPort(dim=1536).embed(["x"])
    assert len(vector) == 1536


def test_stub_embedding_is_l2_normalized() -> None:
    [vector] = StubEmbeddingPort().embed(["some evidence text here"])
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-6)


def test_identical_text_has_cosine_similarity_one() -> None:
    first, second = StubEmbeddingPort().embed(["polymarket forecast", "polymarket forecast"])
    cosine = sum(a * b for a, b in zip(first, second, strict=True))
    assert math.isclose(cosine, 1.0, rel_tol=1e-6)


def test_make_embedder_defaults_to_the_stub() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://x/y")
    assert isinstance(make_embedder(settings), StubEmbeddingPort)


def test_make_embedder_uses_the_provider_when_configured() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://x/y",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        embedding_api_key="sk-test",
        embedding_base_url="https://api.example.com/v1",
    )
    assert isinstance(make_embedder(settings), ProviderEmbeddingPort)


class _FakeEmbeddingTransport:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.calls: list[dict[str, object]] = []
        self._vectors = vectors

    def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        self.calls.append({"model": model, "texts": texts})
        return self._vectors


def test_provider_embed_delegates_to_the_transport() -> None:
    transport = _FakeEmbeddingTransport([[0.1, 0.2], [0.3, 0.4]])
    port = ProviderEmbeddingPort(transport, "text-embedding-3-small")
    assert port.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert transport.calls[0] == {"model": "text-embedding-3-small", "texts": ["a", "b"]}


def test_provider_embed_skips_the_transport_for_empty_input() -> None:
    transport = _FakeEmbeddingTransport([])
    assert ProviderEmbeddingPort(transport, "m").embed([]) == []
    assert transport.calls == []
