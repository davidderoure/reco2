"""Embedding-based story similarity index.

Pre-computed embeddings are loaded from a JSON file (data/embeddings.json).
When loaded into the engine, the content-based strategy blends tag-affinity
scores with embedding-based similarity to the user's engaged stories.

The index is optional — if never loaded, the engine behaves exactly as before.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


class EmbeddingIndex:
    """Holds story embeddings and exposes similarity queries."""

    def __init__(self, embeddings: dict[str, list[float]]) -> None:
        # Normalise all vectors at load time so dot product == cosine similarity.
        self._vectors: dict[str, list[float]] = {
            sid: _normalise(vec) for sid, vec in embeddings.items()
        }

    @classmethod
    def load(cls, path: str | Path) -> "EmbeddingIndex":
        with open(path) as f:
            data = json.load(f)
        return cls(data)

    def story_ids(self) -> set[str]:
        return set(self._vectors.keys())

    def similarity(self, id_a: str, id_b: str) -> float:
        """Cosine similarity between two stories; 0.0 if either is unknown."""
        a = self._vectors.get(id_a)
        b = self._vectors.get(id_b)
        if a is None or b is None:
            return 0.0
        return _dot(a, b)

    def mean_similarity(self, story_id: str, reference_ids: list[str]) -> float:
        """Mean cosine similarity from story_id to a list of reference stories."""
        if not reference_ids:
            return 0.0
        scores = [self.similarity(story_id, ref) for ref in reference_ids]
        return sum(scores) / len(scores)

    def nearest(self, reference_ids: list[str], n: int, excluded: set[str]) -> list[str]:
        """Return the n stories most similar to the centroid of reference_ids."""
        if not reference_ids:
            return []
        centroid = _centroid([self._vectors[r] for r in reference_ids if r in self._vectors])
        if centroid is None:
            return []
        scored = [
            (_dot(centroid, vec), sid)
            for sid, vec in self._vectors.items()
            if sid not in excluded
        ]
        scored.sort(reverse=True)
        return [sid for _, sid in scored[:n]]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _centroid(vecs: list[list[float]]) -> list[float] | None:
    if not vecs:
        return None
    dim = len(vecs[0])
    c = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
    return _normalise(c)
