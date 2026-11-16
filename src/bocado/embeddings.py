"""Embeddings de texto.

Produção usa um modelo multilíngue pequeno via fastembed (ONNX, roda em CPU,
sem chamada de API e sem cota). Os testes usam um embedder de hashing
determinístico para não baixar modelo.
"""

from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from typing import Protocol

import numpy as np

DIM = 384


class Embedder(Protocol):
    nome: str

    def embed(self, textos: list[str]) -> np.ndarray: ...


def _normalizar(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class FastEmbedEmbedder:
    def __init__(self, modelo: str, cache_dir: str | None = None):
        self.nome = modelo
        self._modelo = modelo
        self._cache_dir = cache_dir
        self._impl = None
        self._lock = threading.Lock()

    def _carregar(self):
        with self._lock:
            if self._impl is None:
                from fastembed import TextEmbedding

                self._impl = TextEmbedding(self._modelo, cache_dir=self._cache_dir)
        return self._impl

    def embed(self, textos: list[str]) -> np.ndarray:
        impl = self._carregar()
        return _normalizar(np.array(list(impl.embed(textos, batch_size=64)), dtype=np.float32))


def _tokens(texto: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", t)


class HashEmbedder:
    """Bag-of-words com hashing. Só para testes: rápido e determinístico."""

    nome = "hash-bow"

    def embed(self, textos: list[str]) -> np.ndarray:
        m = np.zeros((len(textos), DIM), dtype=np.float32)
        for i, texto in enumerate(textos):
            for tok in _tokens(texto):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                m[i, h % DIM] += 1.0 if (h >> 9) & 1 else -1.0
        return _normalizar(m)


_padrao: Embedder | None = None


def get_embedder() -> Embedder:
    global _padrao
    if _padrao is None:
        from bocado.config import get_settings

        s = get_settings()
        _padrao = FastEmbedEmbedder(s.embedding_model, s.embedding_cache_dir)
    return _padrao


def set_embedder(e: Embedder) -> None:
    global _padrao
    _padrao = e
