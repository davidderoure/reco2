"""Wildcard: biased random favouring serendipity — stories whose tags are
furthest from the user's existing affinity profile, rather than uniform
random noise.
"""

from __future__ import annotations

import random

from ..catalogue import Catalogue
from ..models import WILDCARD, UserModel
from .base import Strategy


class WildcardStrategy(Strategy):
    code = WILDCARD

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

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
            self.rng.shuffle(pool)
            return [s.story_id for s in pool]

        # Distance = inverse of affinity overlap: stories touching tags the
        # user has shown little/no affinity for score higher (= more novel).
        def novelty(story) -> float:
            if not story.tags:
                return 0.0
            return 1.0 - (
                sum(user.tag_affinity.get(tag, 0.0) for tag in story.tags) / len(story.tags)
            )

        weights = [max(novelty(s), 0.01) for s in pool]
        ranked = sorted(zip(pool, weights), key=lambda t: t[1], reverse=True)

        # Weighted shuffle rather than a strict ranking, so it's not
        # deterministic run to run.
        chosen = self.rng.choices(
            [s.story_id for s, _ in ranked],
            weights=[w for _, w in ranked],
            k=len(ranked),
        )
        # De-dup while preserving the weighted order (choices() can repeat).
        seen: set[str] = set()
        ordered: list[str] = []
        for sid in chosen:
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        return ordered
