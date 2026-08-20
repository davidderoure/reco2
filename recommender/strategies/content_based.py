"""Content-based: score unseen stories by similarity to the user's own
tag affinities, built from their connectedness history.

To break the positive-feedback loop where the same top-scored story would
always be returned first, candidates() returns the top POOL_SIZE stories
in a uniformly shuffled order rather than strict rank order. The engine
picks the first N it needs, so the effective recommendation is a uniform
random draw from the top pool — preference-informed but not deterministic.
"""

from __future__ import annotations

import random
import threading

from ..catalogue import Catalogue
from ..embeddings import EmbeddingIndex
from ..models import CONTENT_BASED, UserModel
from .base import Strategy

POOL_SIZE = 6

# Weight given to embedding similarity vs tag affinity when both are available.
# 0.0 = pure tag affinity (old behaviour); 1.0 = pure embedding similarity.
EMBEDDING_WEIGHT = 0.5


class ContentBasedStrategy(Strategy):
    code = CONTENT_BASED

    def __init__(
        self,
        rng: random.Random | None = None,
        embedding_index: EmbeddingIndex | None = None,
    ) -> None:
        self.rng = rng or random.Random()
        self._rng_lock = threading.Lock()
        self._embeddings = embedding_index

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        has_engaged = any(
            e.connectedness is not None and e.connectedness > 0.5
            for e in user.story_history.values()
        )
        if not user.tag_affinity and not (self._embeddings is not None and has_engaged):
            return []  # cold start: nothing to go on, let other strategies cover this user

        # Reference stories: those the user has engaged with and scored positively.
        engaged_ids = [
            sid for sid, entry in user.story_history.items()
            if entry.connectedness is not None and entry.connectedness > 0.5
        ]

        scored: list[tuple[float, str]] = []
        for story in catalogue.all_stories():
            if story.story_id in excluded:
                continue

            # Tag-affinity score (existing behaviour).
            if story.tags:
                tag_score = sum(user.tag_affinity.get(tag, 0.0) for tag in story.tags) / len(story.tags)
            else:
                tag_score = 0.0

            # Embedding similarity score (blended in when index is available).
            if self._embeddings is not None and engaged_ids:
                emb_score = self._embeddings.mean_similarity(story.story_id, engaged_ids)
                score = (1 - EMBEDDING_WEIGHT) * tag_score + EMBEDDING_WEIGHT * emb_score
            else:
                emb_score = 0.0
                score = tag_score

            if score > 0 or emb_score > 0:
                scored.append((score, story.story_id))

        scored.sort(reverse=True)
        pool = [story_id for _, story_id in scored[:POOL_SIZE]]
        remainder = [story_id for _, story_id in scored[POOL_SIZE:]]
        with self._rng_lock:
            self.rng.shuffle(pool)
        return pool + remainder
