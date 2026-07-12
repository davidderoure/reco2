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
from ..models import CONTENT_BASED, UserModel
from .base import Strategy

POOL_SIZE = 6


class ContentBasedStrategy(Strategy):
    code = CONTENT_BASED

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._rng_lock = threading.Lock()

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        if not user.tag_affinity:
            return []  # cold start: nothing to go on, let other strategies cover this user

        scored: list[tuple[float, str]] = []
        for story in catalogue.all_stories():
            if story.story_id in excluded:
                continue
            if not story.tags:
                continue
            score = sum(user.tag_affinity.get(tag, 0.0) for tag in story.tags) / len(story.tags)
            if score > 0:
                scored.append((score, story.story_id))

        scored.sort(reverse=True)
        pool = [story_id for _, story_id in scored[:POOL_SIZE]]
        remainder = [story_id for _, story_id in scored[POOL_SIZE:]]
        with self._rng_lock:
            self.rng.shuffle(pool)
        return pool + remainder
