"""Topical: defaults to newest stories. Designed as a seam for a future
manual boost list (current-events correlation) without changing callers.
"""

from __future__ import annotations

from ..catalogue import Catalogue
from ..models import TOPICAL, UserModel
from .base import Strategy


class TopicalStrategy(Strategy):
    code = TOPICAL

    def __init__(self, boosted_story_ids: list[str] | None = None) -> None:
        # Manually curated, e.g. by trial staff reacting to current events.
        # Ranked ahead of the newest-stories default when present.
        self.boosted_story_ids = boosted_story_ids or []

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        boosted = [sid for sid in self.boosted_story_ids if sid not in excluded]
        newest = [s.story_id for s in catalogue.newest(len(catalogue)) if s.story_id not in excluded]
        seen = set(boosted)
        return boosted + [sid for sid in newest if sid not in seen]
