"""Content-based: score unseen stories by similarity to the user's own
tag affinities, built from their connectedness history.
"""

from __future__ import annotations

from ..catalogue import Catalogue
from ..models import CONTENT_BASED, UserModel
from .base import Strategy


class ContentBasedStrategy(Strategy):
    code = CONTENT_BASED

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
        return [story_id for _, story_id in scored]
