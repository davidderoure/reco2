"""Synthetic catalogue generation using the definitive initial ORIGIN tag
vocabulary (provided 2026-07-08): one format tag per story (story media
type) plus one or more theme tags (what the story is about).
"""

from __future__ import annotations

import random
import time

from recommender.catalogue import Catalogue
from recommender.models import Story

FORMAT_TAGS = ["Written", "Audio", "Visual", "Video"]

THEME_TAGS = [
    "Literature",
    "Activism",
    "Performing Arts",
    "Friendship",
    "Heritage",
    "Community",
    "WWII",
    "Academia",
    "Animals",
    "Mental Health",
    "Archaeology",
    "Family",
    "Nature",
    "Medicine",
    "Gender",
    "Migration",
    "Black Experience",
    "Industry",
    "Music",
    "LGBTQ+ Experience",
    "Adventure",
    "Sports",
    "Refugee Experience",
    "Women's Suffrage",
    "Craftsmanship",
    "Spirituality",
    "Poetry",
    "South Asian Experience",
    "SE Asian Experience",
    "Latino Experience",
    "Visual Arts",
    "Experience of Disability",
    "Science",
    "Religion",
    "WWI",
    "East Asian Experience",
    "African Experience",
    "Fashion",
    "Technology",
    "Working Class Experience",
    "Childhood",
    "Feeling Unheard",
    "Feeling Lonely",
    "Missed Chances",
    "Feeling Unsupported",
    "Being Judged",
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
