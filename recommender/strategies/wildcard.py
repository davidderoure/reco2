"""Wildcard: biased random favouring serendipity — stories whose tags are
furthest from the user's existing affinity profile, rather than uniform
random noise.
"""

from __future__ import annotations

import random
import threading

from ..catalogue import Catalogue
from ..models import WILDCARD, UserModel
from .base import Strategy


class WildcardStrategy(Strategy):
    code = WILDCARD

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
        pool = [s for s in catalogue.all_stories() if s.story_id not in excluded]
        if not pool:
            return []

        if not user.tag_affinity:
            with self._rng_lock:
                self.rng.shuffle(pool)
            return [s.story_id for s in pool]

        # "Novel" means genuinely unexplored, not "rated low". A tag the
        # user has never encountered gets a high score (that's the actual
        # serendipity target); a tag they've encountered and rated low is
        # a real, explicit signal — not blind unexplored territory — and
        # must not be inverted into a boost. Conflating the two would mean
        # wildcard actively chases tags the user is avoiding (open
        # question #2: lean cautious on avoided tags), which is the
        # opposite of the decided default. See tests for the regression
        # this guards against.
        def novelty(story) -> float:
            if not story.tags:
                return 0.0
            values = [
                1.0 if tag not in user.tag_affinity else user.tag_affinity[tag]
                for tag in story.tags
            ]
            return sum(values) / len(values)

        weights = [max(novelty(s), 0.01) for s in pool]

        # Weighted random permutation (Efraimidis-Spirakis): each item gets
        # a key = U^(1/weight) for U ~ Uniform(0,1), then sort descending.
        # This is a real weighted shuffle without replacement, so a
        # low-weight item is reliably (probabilistically) ranked low
        # rather than landing anywhere by chance — sampling with
        # replacement + dedup (the previous approach) doesn't have that
        # property: a rare item, when it survives at all, can land at any
        # rank. See test_wildcard.py for the regression this guards.
        with self._rng_lock:
            keyed = [
                (self.rng.random() ** (1.0 / w), story.story_id)
                for story, w in zip(pool, weights)
            ]
        keyed.sort(reverse=True)
        return [story_id for _, story_id in keyed]
