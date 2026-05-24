"""Tests for embedding layer: provider factory and embedding store."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from embeddings import (
    EmbeddingConfig,
    EmbeddingStore,
    StubEmbeddingProvider,
    create_embedding_provider,
)


def _stub_store(dims: int = 8) -> EmbeddingStore:
    provider = StubEmbeddingProvider(dimensions=dims)
    return EmbeddingStore(provider, use_faiss=False)


class TestProviderFactory:
    def test_create_stub_provider(self):
        config = EmbeddingConfig(provider="stub", dimensions=16)
        provider = create_embedding_provider(config)
        vecs = provider.embed(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 16

    def test_unknown_provider_raises(self):
        config = EmbeddingConfig(provider="unknown_xyz")
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            create_embedding_provider(config)

    def test_stub_embed_one(self):
        provider = StubEmbeddingProvider(dimensions=4)
        vec = provider.embed_one("test")
        assert vec == [0.0, 0.0, 0.0, 0.0]
        assert provider.dimensions == 4


class TestEmbeddingStore:
    def test_add_and_search(self):
        store = _stub_store()
        store.add("mod.func_a", "def func_a(): process input")
        store.add("mod.func_b", "def func_b(): validate output")
        # Stub returns zero vectors — all cosine similarities are 0
        results = store.search("process input", top_k=5)
        # With zero vectors we expect 0.0 scores but no crash
        assert isinstance(results, list)

    def test_add_batch(self):
        store = _stub_store()
        store.add_batch(
            names=["mod.a", "mod.b"],
            texts=["function a", "function b"],
            metadata=[{}, {"tag": "test"}],
        )
        assert store.size() == 2

    def test_remove(self):
        store = _stub_store()
        store.add("mod.x", "some text")
        assert store.size() == 1
        removed = store.remove("mod.x")
        assert removed is True
        assert store.size() == 0

    def test_remove_missing(self):
        store = _stub_store()
        assert store.remove("does.not.exist") is False

    def test_clear(self):
        store = _stub_store()
        store.add("a", "text a")
        store.add("b", "text b")
        store.clear()
        assert store.size() == 0

    def test_get_item(self):
        store = _stub_store()
        store.add("mod.func", "some code", metadata={"file": "mod.py"})
        item = store.get("mod.func")
        assert item is not None
        assert item.qualified_name == "mod.func"
        assert item.metadata["file"] == "mod.py"

    def test_search_empty_store(self):
        store = _stub_store()
        results = store.search("anything")
        assert results == []
