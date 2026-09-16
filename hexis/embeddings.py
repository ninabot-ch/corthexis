"""Pluggable embedding backends.

One function matters: :func:`embed`, which turns a list of strings into a list
of unit-normalized vectors. Everything else in hexis treats embeddings as a
black box, so swapping backends never touches the index or the server.

Pick one with ``HEXIS_EMBED_BACKEND``:

``local`` (default)
    ``sentence-transformers`` in-process. No infrastructure, works offline after
    the first model download. This is the right choice for a single machine.

``http``
    A service exposing ``POST {HEXIS_EMBED_URL}/api/v1/embed/text`` that accepts
    ``{"texts": [...]}`` and returns ``{"embeddings": [[...], ...]}``. Useful
    when the model lives on a GPU box separate from where notes are edited.

``openai``
    Any OpenAI-compatible ``POST {HEXIS_EMBED_URL}/embeddings`` endpoint
    (OpenAI itself, or a local server that speaks the same dialect).
    Reads ``HEXIS_EMBED_API_KEY``.

The default model is multilingual on purpose: notes written in one language
should be recalled by queries in another. If you change it, re-index with
``--rebuild`` — vectors from two different models are not comparable.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

BACKEND = os.environ.get("HEXIS_EMBED_BACKEND", "local").strip().lower()
MODEL = os.environ.get("HEXIS_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
URL = os.environ.get("HEXIS_EMBED_URL", "http://127.0.0.1:8001").rstrip("/")
API_KEY = os.environ.get("HEXIS_EMBED_API_KEY", "")
BATCH = int(os.environ.get("HEXIS_EMBED_BATCH", "64"))


class EmbeddingError(RuntimeError):
    """The backend could not produce vectors. Callers decide whether to degrade."""


def normalize(v) -> list[float]:
    """Unit-normalize so cosine similarity is a plain dot product at query time.

    Returns built-in floats: numpy scalars (what sentence-transformers hands
    back) are not JSON-serializable, and the vectors are stored as JSON.
    """
    out = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / n for x in out]


@lru_cache(maxsize=1)
def _local_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise EmbeddingError(
            "backend 'local' needs sentence-transformers: "
            "pip install 'hexis[local]' (or set HEXIS_EMBED_BACKEND=http)"
        ) from exc
    return SentenceTransformer(MODEL)


def _embed_local(texts: list[str]) -> list[list[float]]:
    vecs = _local_model().encode(texts, batch_size=BATCH, show_progress_bar=False)
    return [normalize(v) for v in vecs]


def _embed_http(texts: list[str]) -> list[list[float]]:
    import httpx

    out: list[list[float]] = []
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            try:
                resp = client.post(f"{URL}/api/v1/embed/text", json={"texts": batch})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding service unreachable ({URL}): {exc}") from exc
            vecs = resp.json().get("embeddings") or []
            if len(vecs) != len(batch):
                raise EmbeddingError(f"expected {len(batch)} vectors, got {len(vecs)}")
            out.extend(normalize(v) for v in vecs)
    return out


def _embed_openai(texts: list[str]) -> list[list[float]]:
    import httpx

    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    out: list[list[float]] = []
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            try:
                resp = client.post(
                    f"{URL}/embeddings", headers=headers,
                    json={"model": MODEL, "input": batch},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding endpoint failed ({URL}): {exc}") from exc
            data = sorted(resp.json().get("data") or [], key=lambda d: d.get("index", 0))
            if len(data) != len(batch):
                raise EmbeddingError(f"expected {len(batch)} vectors, got {len(data)}")
            out.extend(normalize(d["embedding"]) for d in data)
    return out


_BACKENDS = {"local": _embed_local, "http": _embed_http, "openai": _embed_openai}


def embed(texts: list[str]) -> list[list[float]]:
    """Embed ``texts``, returning one unit-normalized vector per input."""
    if not texts:
        return []
    try:
        fn = _BACKENDS[BACKEND]
    except KeyError:
        raise EmbeddingError(
            f"unknown HEXIS_EMBED_BACKEND={BACKEND!r}; "
            f"expected one of {', '.join(sorted(_BACKENDS))}"
        ) from None
    return fn(texts)


def embed_one(text: str) -> list[float]:
    """Embed a single string. Convenience wrapper around :func:`embed`."""
    return embed([text])[0]


def describe() -> str:
    """Human-readable backend identity, for error messages and selfcheck output."""
    where = "" if BACKEND == "local" else f" @ {URL}"
    return f"{BACKEND}:{MODEL}{where}"
