"""Synthetic catalogue generation using the preliminary ORIGIN tag
vocabulary (provided 2026-06-22): one format tag per story (story media
type) plus one or more theme tags (what the story is about).

This is a placeholder vocabulary for testing logic, not the final
trial tag set — regenerate once the real list is confirmed.
"""

from __future__ import annotations

import random
import time

from recommender.catalogue import Catalogue
from recommender.models import Story

FORMAT_TAGS = ["Written", "Audio", "Visual", "Video"]

THEME_TAGS = [
    "Not Heard",
    "Lonely",
    "Missed Chances",
    "Not Supported",
    "Judged",
    "Hiding Self",
]

ALL_TAGS = FORMAT_TAGS + THEME_TAGS


def generate_catalogue(n_stories: int = 120, seed: int = 0, now: float | None = None) -> Catalogue:
    """One format tag + 1-3 theme tags per story, with spread creation
    dates so `topical` has something meaningful to rank on.
    """
    now = now if now is not None else time.time()
    rng = random.Random(seed)
    stories = []
    for i in range(n_stories):
        format_tag = rng.choice(FORMAT_TAGS)
        n_themes = rng.randint(1, 3)
        theme_tags = rng.sample(THEME_TAGS, k=n_themes)
        age_days = rng.randint(0, 180)
        stories.append(
            Story(
                story_id=f"story-{i:04d}",
                title=f"Story {i}: {', '.join(theme_tags)}",
                tags=[format_tag] + theme_tags,
                created_at=now - age_days * 86400,
                updated_at=now - age_days * 86400,
            )
        )
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue
