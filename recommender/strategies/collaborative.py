"""Collaborative filtering: find users with similar connectedness ratings
on shared stories, then recommend stories those neighbours rated highly
that this user hasn't seen.

Simple user-user cosine similarity over the sparse story_id -> connectedness
rating vectors. Fine for a trial-scale population (~1500 users); would need
a smarter index (e.g. item-item, or precomputed similarity) at larger scale.
"""

from __future__ import annotations

import math

from ..catalogue import Catalogue
from ..models import COLLABORATIVE, UserModel
from .base import Strategy


def _ratings(user: UserModel) -> dict[str, float]:
    return {
        sid: entry.connectedness
        for sid, entry in user.story_history.items()
        if entry.connectedness is not None
    }


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    shared = a.keys() & b.keys()
    if not shared:
        return 0.0
    dot = sum(a[k] * b[k] for k in shared)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class CollaborativeStrategy(Strategy):
    code = COLLABORATIVE
    MIN_SHARED_RATINGS = 1
    MAX_NEIGHBOURS = 20

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        my_ratings = _ratings(user)
        if not my_ratings:
            return []  # nothing to compare on yet

        neighbours: list[tuple[float, UserModel]] = []
        for other_id, other in population.items():
            if other_id == user.user_id:
                continue
            other_ratings = _ratings(other)
            if len(other_ratings.keys() & my_ratings.keys()) < self.MIN_SHARED_RATINGS:
                continue
            sim = _cosine_similarity(my_ratings, other_ratings)
            if sim > 0:
                neighbours.append((sim, other))

        if not neighbours:
            return []

        neighbours.sort(key=lambda t: t[0], reverse=True)
        neighbours = neighbours[: self.MAX_NEIGHBOURS]

        story_scores: dict[str, float] = {}
        for sim, other in neighbours:
            for sid, entry in other.story_history.items():
                if sid in excluded or entry.connectedness is None:
                    continue
                story_scores[sid] = story_scores.get(sid, 0.0) + sim * entry.connectedness

        ranked = sorted(story_scores.items(), key=lambda t: t[1], reverse=True)
        return [story_id for story_id, _ in ranked]
