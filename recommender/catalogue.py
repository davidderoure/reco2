"""In-memory story catalogue, refreshed from the C# StoryService."""

from __future__ import annotations

from .models import Story


class Catalogue:
    def __init__(self) -> None:
        self._stories: dict[str, Story] = {}

    def load(self, stories: list[Story]) -> None:
        """Replace the catalogue wholesale, e.g. on startup or periodic refresh."""
        self._stories = {s.story_id: s for s in stories}

    def upsert(self, story: Story) -> None:
        self._stories[story.story_id] = story

    def get(self, story_id: str) -> Story | None:
        return self._stories.get(story_id)

    def all_ids(self) -> list[str]:
        return list(self._stories.keys())

    def all_stories(self) -> list[Story]:
        return list(self._stories.values())

    def newest(self, n: int) -> list[Story]:
        return sorted(self._stories.values(), key=lambda s: s.created_at, reverse=True)[:n]

    def __len__(self) -> int:
        return len(self._stories)
