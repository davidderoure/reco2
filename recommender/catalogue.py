"""In-memory story catalogue, refreshed from the C# StoryService."""

from __future__ import annotations

import threading

from .models import Story


class Catalogue:
    def __init__(self) -> None:
        self._stories: dict[str, Story] = {}
        self._lock = threading.RLock()

    def load(self, stories: list[Story]) -> None:
        """Replace the catalogue wholesale, e.g. on startup or periodic refresh."""
        new = {s.story_id: s for s in stories}
        with self._lock:
            self._stories = new

    def upsert(self, story: Story) -> None:
        with self._lock:
            self._stories[story.story_id] = story

    def get(self, story_id: str) -> Story | None:
        with self._lock:
            return self._stories.get(story_id)

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._stories.keys())

    def all_stories(self) -> list[Story]:
        with self._lock:
            return list(self._stories.values())

    def newest(self, n: int) -> list[Story]:
        with self._lock:
            return sorted(self._stories.values(), key=lambda s: s.created_at, reverse=True)[:n]

    def __len__(self) -> int:
        with self._lock:
            return len(self._stories)
