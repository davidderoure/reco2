"""Topical strategy — three-tier selection:

1. Boosted stories (BOOSTED_STORY_IDS env var) — explicit editorial curation,
   wins over everything else when set.
2. Genuinely new stories — published within TOPICAL_NEW_STORY_DAYS (default 30).
   Auto-surfaces fresh content to all users, including near-exhausted ones.
3. Random fallback — when neither boosted nor new stories are available (the
   expected state during a trial with a frozen catalogue). Behaves like a
   second wildcard slot, giving clean uniform coverage for cold-start analysis.
"""

from __future__ import annotations

import random
import time
from datetime import timedelta

from ..catalogue import Catalogue
from ..models import TOPICAL, UserModel
from .base import Strategy

TOPICAL_NEW_STORY_DAYS = 30


class TopicalStrategy(Strategy):
    code = TOPICAL

    def __init__(
        self,
        boosted_story_ids: list[str] | None = None,
        new_story_days: int = TOPICAL_NEW_STORY_DAYS,
    ) -> None:
        self.boosted_story_ids = boosted_story_ids or []
        self.new_story_days = new_story_days

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        boosted = [sid for sid in self.boosted_story_ids if sid not in excluded]
        if boosted:
            return boosted

        cutoff = time.time() - timedelta(days=self.new_story_days).total_seconds()
        recent = [
            s.story_id for s in catalogue.newest(len(catalogue))
            if s.story_id not in excluded and s.created_at >= cutoff
        ]
        if recent:
            rng = random.Random()
            rng.shuffle(recent)
            return recent

        # Frozen catalogue or nothing new — random fallback
        all_candidates = [s.story_id for s in catalogue.all_stories() if s.story_id not in excluded]
        rng = random.Random()
        rng.shuffle(all_candidates)
        return all_candidates
